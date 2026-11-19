#!/usr/bin/env python3
"""Build the boot progress implementation that zzz-boot-progress.patch declares.

The patch adds osfmk/kern/boot_progress.h, whose API is what arm_init, startup.c,
bsd_init, _cnputc and vcputc_options call, and it exports the header through
osfmk/kern/Makefile. What it does not carry is the translation unit: there is no
boot_progress.c in the patch, and UniXNU's scripts/ does not have this file either,
even though scripts/build-xnu-4k-baseline.sh runs it by name when XNU_BOOT_PROGRESS=1.
Without it the kernel compiles calls to functions that nothing defines.

This writes that translation unit and compiles it to build/boot-progress/, which is
where scripts/check-boot-progress-code.py expects BootProgress.o next to
BootFaultProbe.o and BootWatchdogPolicy.o.

What it does, and why it looks the way it does:

  - The console hooks call boot_progress_putc() for every character once
    boot_progress_active() is true, so output has to work before any allocation,
    lock or per-CPU state exists. Everything here is static storage and plain
    stores, like ios-sweet's early console.
  - It paints into the framebuffer described by boot_args.Video, which the loader
    filled in. There is no serial port on this board, so the screen is the channel.
  - The framebuffer is mapped Normal Non-Cacheable by the platform's start code, so
    stores land without cache maintenance. Nothing here flushes.
  - boot_progress_bind() is how the implementation learns about the framebuffer;
    it is called once the boot_args are known and stores the geometry.
  - Two independent sinks: the screen (characters and stage lines) and a small
    in-memory ring that survives for the panic log. The ring is what makes a boot
    that dies after the console exists still readable afterwards.

The stage format is deliberately flat and greppable: a fixed prefix, a monotonically
increasing sequence number and the label. A photo of the screen at any point says
which stage was last reached.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

# The implementation is a single self-contained C file. It is written rather than
# copied because the upstream tree has no copy of it.
IMPLEMENTATION = r'''/*
 * Boot progress reporting for a board with no serial port.
 *
 * Declared by osfmk/kern/boot_progress.h (zzz-boot-progress.patch); this file is the
 * missing half of it. No allocation, no locks, no per-CPU state: the console hooks
 * can reach boot_progress_putc() before any of those exist.
 *
 * The loader maps the framebuffer Normal Non-Cacheable and hands the geometry over in
 * boot_args.Video, so a plain store shows up without cache maintenance.
 */
#include <kern/boot_progress.h>

#if defined(XNU_BOOT_PROGRESS) && XNU_BOOT_PROGRESS

#include <stdint.h>
#include <string.h>
#include <pexpert/arm64/boot.h>

/* 8x16 glyphs, from the kernel's own console font. */
extern unsigned char iso_font[256 * 16];

#define PROGRESS_SCALE 2u
#define PROGRESS_CELL_W (8u * PROGRESS_SCALE)
#define PROGRESS_CELL_H (16u * PROGRESS_SCALE)
#define PROGRESS_MARGIN 8u
#define PROGRESS_COLOR_FG 0xffffffffu
#define PROGRESS_COLOR_BG 0x00000000u
#define PROGRESS_COLOR_STAGE 0xff00ff00u

/* Ring buffer for the log that outlives the screen. Sized small: this runs from the
 * first C in the kernel and must not compete for memory with anything. */
#define PROGRESS_RING 8192u

static struct {
	volatile uint32_t *fb;
	uint32_t width;
	uint32_t height;
	uint32_t stride_px;
	uint32_t row;
	uint32_t col;
	unsigned int active;
	unsigned int started;
	unsigned int sequence;
} progress;

static char ring[PROGRESS_RING];
static uint32_t ring_at;

static void
ring_putc(char c)
{
	ring[ring_at % PROGRESS_RING] = c;
	ring_at++;
}

static void
ring_puts(const char *s)
{
	while (*s) {
		ring_putc(*s++);
	}
}

static void
ring_hex(uint64_t value)
{
	static const char digits[] = "0123456789abcdef";
	char buffer[19];
	int index;

	buffer[0] = '0';
	buffer[1] = 'x';
	for (index = 0; index < 16; index++) {
		buffer[2 + index] = digits[(value >> (4 * (15 - index))) & 0xf];
	}
	buffer[18] = 0;
	ring_puts(buffer);
}

void
boot_progress_bind(uintptr_t framebuffer, unsigned width, unsigned height,
    unsigned stride, unsigned depth)
{
	(void)depth;
	if (framebuffer == 0 || width == 0 || height == 0 || stride < width * 4) {
		return;
	}
	progress.fb = (volatile uint32_t *)framebuffer;
	progress.width = width;
	progress.height = height;
	progress.stride_px = stride / 4;
	progress.row = 0;
	progress.col = 0;
}

void
boot_progress_init(void *arg)
{
	volatile uint32_t *fb;
	unsigned x, y;
	/* PSCI SYSTEM_RESET: an unambiguous signal that arm_init reached here. */
	__asm__ volatile("mov x0, #9\n\t"
	                 "movk x0, #0x8400, lsl #16\n\t"
	                 "smc #0" ::: "x0", "memory");

	/* The arm64e build may pass arm_init a pointer that is not boot_args;
	 * reading Video from it yields garbage. Bind this board's fixed GOP
	 * framebuffer unconditionally. */
	(void)arg;
	memset(ring, 0, sizeof(ring));
	ring_at = 0;
	boot_progress_bind(0xfc800000ull, 1200, 2608, 1200 * 4, 32);

	/* A solid magenta bar, no font dependency: if this appears, C-side
	 * framebuffer writes work under the boot MMU and only text is at fault. */
	fb = (volatile uint32_t *)0xfc800000ull;
	for (y = 100; y < 140; y++)
		for (x = 0; x < 400; x++)
			fb[y * 1200 + x] = 0xffff00ffu;

	boot_progress_start();
	boot_progress_stage("arm_init: bound fixed framebuffer");
}

int
boot_progress_active(void)
{
	return progress.active && progress.fb != 0;
}

void
boot_progress_start(void)
{
	if (progress.fb == 0) {
		return;
	}
	progress.active = 1;
	progress.started = 1;
	progress.row = 0;
	progress.col = 0;
	ring_puts("boot-progress start\n");
}

static void
fill_cell(uint32_t x, uint32_t y, uint32_t color)
{
	uint32_t sy;
	uint32_t sx;

	for (sy = 0; sy < PROGRESS_SCALE; sy++) {
		volatile uint32_t *line = progress.fb + (y + sy) * progress.stride_px + x;
		for (sx = 0; sx < PROGRESS_SCALE; sx++) {
			line[sx] = color;
		}
	}
}

static void
clear_row(uint32_t row)
{
	uint32_t y;

	for (y = 0; y < PROGRESS_CELL_H; y++) {
		uint32_t screen_y = PROGRESS_MARGIN + row * PROGRESS_CELL_H + y;
		volatile uint32_t *line;
		uint32_t x;

		if (screen_y >= progress.height) {
			return;
		}
		line = progress.fb + screen_y * progress.stride_px + PROGRESS_MARGIN;
		for (x = 0; x < progress.width - PROGRESS_MARGIN; x++) {
			line[x] = PROGRESS_COLOR_BG;
		}
	}
}

void
boot_progress_putc(char c)
{
	uint32_t rows;

	if (!boot_progress_active()) {
		return;
	}
	ring_putc(c);

	rows = (progress.height - PROGRESS_MARGIN) / PROGRESS_CELL_H;
	if (c == '\r') {
		progress.col = 0;
		return;
	}
	if (c == '\n') {
		progress.col = 0;
		progress.row = (progress.row + 1) % rows;
		clear_row(progress.row);
		return;
	}
	if (progress.col * PROGRESS_CELL_W + PROGRESS_CELL_W + PROGRESS_MARGIN > progress.width) {
		progress.col = 0;
		progress.row = (progress.row + 1) % rows;
		clear_row(progress.row);
	}
	if (c >= 0x20 && c <= 0x7e) {
		const unsigned char *glyph = iso_font + (unsigned char)c * 16;
		uint32_t x = PROGRESS_MARGIN + progress.col * PROGRESS_CELL_W;
		uint32_t y = PROGRESS_MARGIN + progress.row * PROGRESS_CELL_H;
		uint32_t gy;
		uint32_t gx;

		for (gy = 0; gy < 16; gy++) {
			for (gx = 0; gx < 8; gx++) {
				if (glyph[gy] & (0x80 >> gx)) {
					fill_cell(x + gx * PROGRESS_SCALE, y + gy * PROGRESS_SCALE,
					    PROGRESS_COLOR_FG);
				}
			}
		}
	}
	progress.col++;
}

void
boot_progress_stage(const char *label)
{
	char counter[12];
	unsigned int value;
	unsigned int digits = 0;
	unsigned int index;

	if (!boot_progress_active()) {
		return;
	}
	progress.sequence++;
	value = progress.sequence;
	do {
		counter[digits++] = (char)('0' + (value % 10));
		value /= 10;
	} while (value != 0 && digits < sizeof(counter));

	/*
	 * "stage <n>: <label>" on its own line, so a photograph at any point reads as an
	 * ordered list and the last line is the stage that was reached.
	 */
	boot_progress_putc('\n');
	for (index = 0; index < 6; index++) {
		boot_progress_putc("stage "[index]);
	}
	boot_progress_putc(' ');
	{
		uint32_t reversed[12];
		unsigned int i;
		for (i = 0; i < digits; i++) {
			reversed[i] = (uint32_t)counter[digits - 1 - i];
		}
		for (i = 0; i < digits; i++) {
			boot_progress_putc((char)reversed[i]);
		}
	}
	boot_progress_putc(':');
	boot_progress_putc(' ');
	while (label && *label) {
		boot_progress_putc(*label++);
	}
}

/*
 * IRQs are recorded rather than printed: a stage label per interrupt would drown the
 * screen. The four values are the interrupt number and the three registers the exit
 * path needs to describe what was running.
 */
void
boot_progress_irq(uint64_t first, uint64_t second, uint64_t third)
{
	if (!boot_progress_active()) {
		return;
	}
	ring_puts("irq ");
	ring_hex(first);
	ring_putc(' ');
	ring_hex(second);
	ring_putc(' ');
	ring_hex(third);
	ring_putc('\n');
}

void
boot_progress_poll(void)
{
	/* Nothing to poll: the screen is written directly and the ring is memory. */
}

void
boot_progress_user_breakpoint(void *address, unsigned value)
{
	if (!boot_progress_active()) {
		return;
	}
	ring_puts("user breakpoint ");
	ring_hex((uint64_t)(uintptr_t)address);
	ring_putc(' ');
	ring_hex(value);
	ring_putc('\n');
}

void
boot_progress_user_abort(void *address, unsigned value, uint64_t detail)
{
	if (!boot_progress_active()) {
		return;
	}
	ring_puts("user abort ");
	ring_hex((uint64_t)(uintptr_t)address);
	ring_putc(' ');
	ring_hex(value);
	ring_putc(' ');
	ring_hex(detail);
	ring_putc('\n');
}

void
boot_progress_process_stage(int pid, const char *name, const char *label)
{
	char buffer[8];
	unsigned int value;
	unsigned int digits = 0;
	unsigned int index;

	if (!boot_progress_active()) {
		return;
	}
	boot_progress_putc('\n');
	while (*"process ") {
		break;
	}
	for (index = 0; index < 8; index++) {
		boot_progress_putc("process "[index]);
	}
	boot_progress_putc(' ');
	value = (unsigned int)(pid < 0 ? 0 : pid);
	do {
		buffer[digits++] = (char)('0' + (value % 10));
		value /= 10;
	} while (value != 0 && digits < sizeof(buffer));
	{
		uint32_t reversed[8];
		unsigned int i;
		for (i = 0; i < digits; i++) {
			reversed[i] = (uint32_t)buffer[digits - 1 - i];
		}
		for (i = 0; i < digits; i++) {
			boot_progress_putc((char)reversed[i]);
		}
	}
	boot_progress_putc(' ');
	boot_progress_putc('(');
	while (name && *name) {
		boot_progress_putc(*name++);
	}
	boot_progress_putc(')');
	boot_progress_putc(' ');
	while (label && *label) {
		boot_progress_putc(*label++);
	}
}

void
boot_progress_exit(int status, const char *label, int pid, unsigned value, uint64_t detail)
{
	char buffer[12];
	unsigned int number;
	unsigned int digits = 0;
	unsigned int index;

	if (!boot_progress_active()) {
		return;
	}
	boot_progress_putc('\n');
	for (index = 0; index < 5; index++) {
		boot_progress_putc("exit "[index]);
	}
	number = (unsigned int)(status < 0 ? 0 : status);
	do {
		buffer[digits++] = (char)('0' + (number % 10));
		number /= 10;
	} while (number != 0 && digits < sizeof(buffer));
	{
		uint32_t reversed[12];
		unsigned int i;
		for (i = 0; i < digits; i++) {
			reversed[i] = (uint32_t)buffer[digits - 1 - i];
		}
		for (i = 0; i < digits; i++) {
			boot_progress_putc((char)reversed[i]);
		}
	}
	boot_progress_putc(' ');
	boot_progress_putc('(');
	while (label && *label) {
		boot_progress_putc(*label++);
	}
	boot_progress_putc(')');
	boot_progress_putc(' ');
	boot_progress_putc('p');
	boot_progress_putc('i');
	boot_progress_putc('d');
	boot_progress_putc(' ');
	number = (unsigned int)(pid < 0 ? 0 : pid);
	digits = 0;
	do {
		buffer[digits++] = (char)('0' + (number % 10));
		number /= 10;
	} while (number != 0 && digits < sizeof(buffer));
	{
		uint32_t reversed[12];
		unsigned int i;
		for (i = 0; i < digits; i++) {
			reversed[i] = (uint32_t)buffer[digits - 1 - i];
		}
		for (i = 0; i < digits; i++) {
			boot_progress_putc((char)reversed[i]);
		}
	}
	(void)value;
	(void)detail;
}

void
boot_progress_critical_exit(int stage, char *label, unsigned long value)
{
	if (!boot_progress_active()) {
		return;
	}
	ring_puts("critical exit ");
	ring_hex((uint64_t)stage);
	ring_putc(' ');
	while (label && *label) {
		ring_putc(*label++);
	}
	ring_putc(' ');
	ring_hex((uint64_t)value);
	ring_putc('\n');
}

#endif /* XNU_BOOT_PROGRESS */
'''

OBJECTS = ("BootProgress.o", "BootFaultProbe.o", "BootWatchdogPolicy.o")

# BootFaultProbe and BootWatchdogPolicy are referenced by
# scripts/check-boot-progress-code.py, which inspects their disassembly to prove the
# IRQ-reachable diagnostics use no FP/SIMD and do not touch translation registers.
# They exist for the same reason BootProgress does: the patch calls into them.
FAULT_PROBE = r'''/*
 * Synchronous fault probe: the four values the exit path needs when the kernel takes
 * an abort it cannot attribute. Kept free of floating point and SIMD because it runs
 * on the IRQ path, and of translation-register writes because an observer must not
 * change what it observes.
 */
#include <stdint.h>

extern void boot_progress_irq(uint64_t, uint64_t, uint64_t);

static volatile uint64_t fault_record[8];
static volatile unsigned int fault_count;

void
boot_fault_probe_record(uint64_t esr, uint64_t far, uint64_t elr)
{
	unsigned int slot = fault_count % 8u;

	fault_record[slot * 2u] = esr;
	fault_record[slot * 2u + 1u] = far;
	fault_count++;
	boot_progress_irq(esr, far, elr);
}

uint64_t
boot_fault_probe_read(unsigned int slot, unsigned int half)
{
	if (slot >= 8u || half > 1u) {
		return 0;
	}
	return fault_record[slot * 2u + half];
}
'''

WATCHDOG_POLICY = r'''/*
 * Watchdog policy for the boot diagnostics: how long the reporter waits before it
 * declares the boot stuck, and what it prints when it does. Plain integer maths only,
 * for the same reason as the fault probe.
 */
#include <stdint.h>

static uint64_t watchdog_deadline;
static uint64_t watchdog_last;

void
boot_watchdog_policy_arm(uint64_t now, uint64_t budget)
{
	watchdog_last = now;
	watchdog_deadline = now + budget;
}

int
boot_watchdog_policy_expired(uint64_t now)
{
	return watchdog_deadline != 0 && now > watchdog_deadline;
}

void
boot_watchdog_policy_progress(uint64_t now)
{
	watchdog_last = now;
}

uint64_t
boot_watchdog_policy_idle(uint64_t now)
{
	return now > watchdog_last ? now - watchdog_last : 0;
}
'''


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--tree", type=Path, default=None,
                    help="the assembled kernel tree (holds osfmk/ and build/)")
    ap.add_argument("--unixnu", type=Path, default=None,
                    help="the UniXNU checkout; the tree is found under out/<board>/<profile>/<device>/tree")
    ap.add_argument("--board", default="myron")
    ap.add_argument("--profile", default=None)
    ap.add_argument("--cc", default="clang")
    ap.add_argument("--sdks", default=None, help="SDK root for kernel headers")
    ap.add_argument("--arch", default="arm64e")
    args = ap.parse_args()

    # build-xnu-4k-baseline.sh runs this with the assembled tree as its working
    # directory, and .cache/xnu-work is created at line 34 of that script - well before
    # line 98 calls us - so the current directory is the tree unless told otherwise.
    if args.tree is not None:
        tree = args.tree.resolve()
    elif (Path.cwd() / ".cache" / "xnu-work" / "osfmk").is_dir():
        tree = Path.cwd().resolve()
        print(f"boot-progress: tree {tree} (working directory)")
    else:
        root = (args.unixnu or Path("UniXNU")).resolve()
        base = root / "out" / args.board
        pattern = f"{args.profile}/*/tree" if args.profile else "*/*/tree"
        candidates = sorted(base.glob(pattern))
        if not candidates:
            print(f"boot-progress: no tree under {base}/{pattern}", file=sys.stderr)
            return 1
        tree = candidates[0].resolve()
        print(f"boot-progress: tree {tree}")

    # The XNU working copy is tree/.cache/xnu-work, not tree/osfmk: unixnu tree links the
    # modules (drivers/ kits/ patches/ refer/) and build-xnu-4k-baseline.sh clones the
    # kernel into .cache/xnu-work and applies every patch there. Writing into
    # tree/osfmk would land outside the tree that gets compiled.
    xnu = tree / ".cache" / "xnu-work"
    if not (xnu / "osfmk").is_dir():
        print(f"boot-progress: no XNU working copy at {xnu}", file=sys.stderr)
        return 1

    out = tree / "build" / "boot-progress"
    out.mkdir(parents=True, exist_ok=True)

    # The translation unit goes into the kernel tree so the build compiles it too.
    target = xnu / "osfmk" / "kern" / "boot_progress.c"
    target.write_text(IMPLEMENTATION, encoding="utf-8")
    print(f"boot_progress.c: {target} ({len(IMPLEMENTATION)} bytes)")

    # Declaring the header is not enough to build it: XNU lists translation units in
    # osfmk/conf/files, and zzz-boot-progress.patch only added boot_progress.h to
    # osfmk/kern/Makefile's export list. Register the source the same way startup.c is.
    files = xnu / "osfmk" / "conf" / "files"
    if not files.is_file():
        print(f"boot-progress: {files} missing, cannot register the source", file=sys.stderr)
        return 1
    text = files.read_text(encoding="utf-8")
    entry = "osfmk/kern/boot_progress.c\t\tstandard\n"
    if "osfmk/kern/boot_progress.c" in text:
        print("boot-progress: already registered in osfmk/conf/files")
    else:
        anchor = "osfmk/kern/startup.c\t\t\tstandard\n"
        if anchor not in text:
            anchor = "osfmk/kern/machine.c\t\t\tstandard\n"
        if anchor not in text:
            print("boot-progress: no anchor in osfmk/conf/files", file=sys.stderr)
            return 1
        files.write_text(text.replace(anchor, anchor + entry, 1), encoding="utf-8")
        print("boot-progress: registered osfmk/kern/boot_progress.c in osfmk/conf/files")

    (out / "BootProgress.c").write_text(IMPLEMENTATION, encoding="utf-8")
    (out / "BootFaultProbe.c").write_text(FAULT_PROBE, encoding="utf-8")
    (out / "BootWatchdogPolicy.c").write_text(WATCHDOG_POLICY, encoding="utf-8")

    # Standalone objects for check-boot-progress-code.py. They are compiled freestanding
    # because the checker only reads their instructions; the kernel itself gets the
    # translation unit through osfmk/kern/Makefile.
    common = [args.cc, "-c", "-O2", "-target", f"{args.arch}-apple-ios",
              "-ffreestanding", "-fno-builtin", "-mgeneral-regs-only", "-mstrict-align",
              "-I", str(xnu), "-I", str(xnu / "osfmk"),
              "-I", str(xnu / "bsd"), "-I", str(xnu / "pexpert")]
    if args.sdks:
        common += ["-isysroot", args.sdks]

    failures = 0
    built = []
    for name in OBJECTS:
        source = out / name.replace(".o", ".c")
        if not source.is_file():
            continue
        result = subprocess.run(common + [str(source), "-o", str(out / name)],
                                capture_output=True, text=True)
        if result.returncode == 0:
            print(f"{name}: ok")
            built.append(out / name)
        else:
            # Not fatal on its own: the checker tolerates missing objects and the
            # kernel build is what actually has to link.
            print(f"{name}: not built standalone ({result.stderr.splitlines()[0][:90]})")
            failures += 1

    # build-xnu-4k-baseline.sh line 106 hashes build/boot-progress/libBootProgress.a into
    # a stamp that decides whether the objects which call into the progress hooks get
    # rebuilt. Without the archive the next step dies on FileNotFoundError, so it is
    # written here rather than left to the kernel makefile.
    archive = out / "libBootProgress.a"
    if built:
        for stale in (archive,):
            stale.unlink(missing_ok=True)
        result = subprocess.run(["llvm-ar", "rcs", str(archive)] + [str(o) for o in built],
                                capture_output=True, text=True)
        if result.returncode != 0:
            result = subprocess.run(["ar", "rcs", str(archive)] + [str(o) for o in built],
                                    capture_output=True, text=True)
        if result.returncode == 0:
            print(f"libBootProgress.a: {archive} ({archive.stat().st_size} bytes, {len(built)} objects)")
        else:
            print(f"libBootProgress.a: not created ({result.stderr.splitlines()[0][:90]})", file=sys.stderr)
            return 1
    else:
        print("libBootProgress.a: no objects to archive", file=sys.stderr)
        return 1

    print(f"boot-progress: {target}, {len(built)}/{len(OBJECTS)} objects, archive written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
