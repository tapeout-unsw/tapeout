# Resident UART monitor on core 0. Core 1 runs words entered at the prompt.
# No stack is used by the monitor. See README.md for the payload ABI.
.equ UART,    0x1000
.equ PAYLOAD, 0x300
.equ LIMIT,   0x3bc             # reserve one RET and 64 bytes of stack

.section .text
.globl _start
_start:
    li   s0, UART
    lw   t0, 12(s0)
    bnez t0, worker
    li   t0, 3                # claim RX, hold core 1 and flush its caches
    sw   t0, 28(s0)
    la   a0, banner
    jal  puts
    li   a5, 0                # release core 1 after the next prompt
reset_line:
    li   s1, PAYLOAD
    li   a2, 0                # word accumulator
    li   a3, 0                # hex digits in current word
    li   a4, 0                # invalid line, discard until newline
    la   a0, prompt
    jal  puts
    # Start the job only after the prompt, so its UART output does not race ours.
    beqz a5, receive
    li   a5, 0
    li   t0, 1
    sw   t0, 28(s0)           # keep console, release core 1 from reset
receive:
    lw   t0, 4(s0)
    andi t1, t0, 4
    beqz t1, 1f
    li   a4, 1                # never execute a line with dropped bytes
1:  andi t0, t0, 2
    beqz t0, receive
    lw   a0, 24(s0)
    li   t0, '!'
    beq  a0, t0, cancel
    li   t0, 13
    beq  a0, t0, newline
    li   t0, 10
    beq  a0, t0, newline
    bnez a4, receive
    li   t0, ' '
    beq  a0, t0, separator
    li   t0, 9
    beq  a0, t0, separator
    # Accept exactly eight hex digits per word, without a 0x prefix.
    addi t1, a0, -48
    li   t0, 10
    bltu t1, t0, digit
    ori  t1, a0, 32
    addi t1, t1, -97
    li   t0, 6
    bgeu t1, t0, invalid
    addi t1, t1, 10
digit:
    li   t0, 8
    bgeu a3, t0, invalid
    # The first digit of a line stops the previous job before its instructions
    # are edited. Write CONTROL only once: each write clears RX overflow, so a
    # write per digit could hide bytes dropped after the status poll.
    bnez a3, 2f
    li   t0, PAYLOAD
    bne  s1, t0, 2f
    li   t0, 3
    sw   t0, 28(s0)
2:  slli a2, a2, 4
    or   a2, a2, t1
    addi a3, a3, 1
    j    receive
separator:
    beqz a3, receive
    jal  commit
    j    receive
newline:
    bnez a4, bad_line
    beqz a3, launch
    jal  commit
    bnez a4, bad_line
launch:
    li   t0, PAYLOAD
    beq  s1, t0, receive   # empty line, including LF following CR
    li   t0, 0x00008067    # append RET so a straight-line snippet returns
    sw   t0, 0(s1)
    la   t0, job
    li   t1, 1
    sw   t1, 0(t0)
    li   a5, 1             # release core 1 once the prompt is out
    j    reset_line
invalid:
    li   a4, 1
    j    receive
bad_line:
    la   a0, error
    jal  puts
cancel:
    li   t0, 3
    sw   t0, 28(s0)
    j    reset_line

# Commit one complete word. a4 records an invalid or oversized line.
commit:
    li   t0, 8
    bne  a3, t0, commit_bad
    li   t0, LIMIT
    bgeu s1, t0, commit_bad
    sw   a2, 0(s1)
    addi s1, s1, 4
    li   a2, 0
    li   a3, 0
    ret
commit_bad:
    li   a4, 1
    ret

# Print a NUL-terminated string. Does not use the stack.
puts:
    lbu  t0, 0(a0)
    beqz t0, puts_done
1:  lw   t1, 4(s0)
    andi t1, t1, 1
    bnez t1, 1b
    sw   t0, 0(s0)
    addi a0, a0, 1
    j    puts
puts_done:
    ret

worker:
    la   t0, job
1:  lw   t1, 0(t0)
    li   t2, 1
    bne  t1, t2, 1b
    li   sp, 0x400
    li   t0, PAYLOAD
    jalr ra, 0(t0)
    la   t0, job
    li   t1, 2
    sw   t1, 0(t0)
park:
    j    park

.section .data
.balign 4
job:    .word 0               # 0 boot, 1 launched, 2 returned
banner: .asciz "BOB OS core0\r\n8-digit hex words run on core1; ! stops\r\n"
prompt: .asciz "\r\nhex> "
error:  .asciz "\r\nERR"
.balign 4
