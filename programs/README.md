```sh
# Build all assembly programs
make

# Run the list of program checks
./test_all.sh
NREGS=32 ./test_all.sh        # Use 32 registers instead of the default 16

# Build and run one program
make run PROGRAMS=mc_memwalk

# Load programs and print UART output without comparing it
make run PROGRAMS="mc_reload_a mc_reload_b"

# Load two programs in order, without resetting the SoC between them
make run PROGRAMS="mc_reload_a mc_reload_b" UART=AP

# Load three programs, including a repeated image
make run PROGRAMS="mc_memwalk mc_uart mc_memwalk" UART=PHELLOWORLDPP

# Assembly filenames are also accepted
make run PROGRAMS="mc_reload_a.S mc_reload_b.S" UART=AP

# Inspect register values after every instruction
make run PROGRAMS=write_regs SIM_ARGS='+TRACE_REGS=1'

# Check selected registers on both cores
make run PROGRAMS=write_regs \
    REGS="0:x5=000000ff 1:x5=0000ff00"
make run PROGRAMS=memory REGS="0:x10=ffffff80 1:x11=00000080"

# Remove generated files
make clean
```
