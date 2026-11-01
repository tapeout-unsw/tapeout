#!/usr/bin/env bash
set -e
cd "$(dirname "${BASH_SOURCE[0]}")"

make run PROGRAMS=write_regs REGS='0:x5=ff 1:x5=ff00 0:x0=0 1:x0=0'
make run PROGRAMS=memory REGS='0:x6=80028001 0:x10=ffffff80 0:x11=80 0:x12=ffff8001 0:x13=8001 0:x14=ffff8002 0:x15=8002 1:x6=80028001 1:x10=ffffff80 1:x11=80 1:x12=ffff8001 1:x13=8001 1:x14=ffff8002 1:x15=8002'
make run PROGRAMS=lock_counter REGS='0:x10=40 1:x10=40 0:x13=0 1:x13=0'
make run PROGRAMS=exit_core0 REGS='0:x8=1000 1:x8=1000 0:x9=0 1:x9=1 1:x10=1'
make run PROGRAMS=exit_order REGS='0:x8=1000 1:x8=1000 0:x9=0 1:x9=1 0:x10=2'
make run PROGRAMS=mc_byte_lanes UART=P
make run PROGRAMS=mc_cache_thrash UART=P
make run PROGRAMS=mc_lock_stress UART=P
make run PROGRAMS=mc_memwalk UART=P
make run PROGRAMS=mc_uart UART=HELLOWORLDP
make run PROGRAMS='mc_reload_a mc_reload_b' UART=AP
make run PROGRAMS='mc_memwalk mc_uart mc_memwalk' UART=PHELLOWORLDPP
