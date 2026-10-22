`timescale 1ns / 1ps
//
// Data cache snoop. Keeps this core's cache coherent with the other requesters
// by watching the bus writes the arbiter grants. Purely combinational: its
// outputs act at the clock edge that ends the cycle of the bus write.
//
// Parameters
//   RAW      RAM byte address bits (10 = 1 KiB, 11 = 2 KiB). Address bits above
//            the RAM size alias onto the same word (addr_decode uses
//            b_addr[RAW-1:2]), so they are ignored when comparing.
//   OWN_SRC  this cache's b_src (1 = core 0's data port, 2 = core 1's). Its
//            own writes are not snooped: the store that caused one updates the
//            line itself.
//
// A bus write that counts (remote_write)
//   b_valid, |b_wstrb, b_addr[12] = 0 (RAM, not MMIO) and b_src != OWN_SRC.
//   That includes the bootloader (b_src = 0). Reads never count.
//
// Invalidate (option B, "snoop-invalidate")
//   The write's index is b_addr[4:2] and its tag is b_addr[10:5]. The storage
//   shows the lines of that set (snoop_index) on its second read port; a way
//   that is valid and has the same tag gets inv0 or inv1, and the storage
//   clears its valid bit at the end of the cycle. A partial write (sb, sh)
//   also drops the whole line: the line is refetched on the next load.
//   The storage must let a fill win over an invalidate of the same entry: that
//   case is another address in the same set, and the fill is the newer line.
//
// Fill race
//   A load miss is granted in cycle N and its data comes back in the FILL cycle
//   N+1, but the SRAM read happened in N. A remote write granted in N+1 changes
//   the word after that read, so the data being filled is already stale. While
//   in_fill is high (the FILL cycle), a remote write to the word at miss_addr
//   raises kill_fill and the controller does not install the line. (A write
//   granted before N is seen by the read; none can be granted in N, which is
//   ours.) The load itself may still return that data to the core.
//
module dcache_snoop #(
    parameter int RAW = 10,
    parameter int OWN_SRC = 1
) (
    // Bus, after the arbiter
    input  logic        b_valid,
    input  logic [12:0] b_addr,
    input  logic [3:0]  b_wstrb,
    input  logic [2:0]  b_src,

    // Second read port of dcache_storage: the lines of the snooped set
    output logic [2:0]  snoop_index,
    input  logic        sec0_valid,
    input  logic [5:0]  sec0_tag,
    input  logic        sec1_valid,
    input  logic [5:0]  sec1_tag,

    // Invalidate the line in that way (at snoop_index) at the end of this cycle
    output logic        inv0,
    output logic        inv1,

    // Fill race, with dcache_FSM
    input  logic        in_fill,        // the FILL cycle of a load miss
    input  logic [12:0] miss_addr,      // address of that load (saved at the grant)
    output logic        kill_fill       // do not install the line
);
    localparam logic [2:0] OWN = 3'(OWN_SRC);

    // Tag bits that exist for this RAM size: tag bit t is address bit t + 5.
    localparam logic [5:0] TAG_MASK = 6'((1 << (RAW - 5)) - 1);

    logic       remote_write;
    logic [5:0] snoop_tag;

    assign remote_write = b_valid && (|b_wstrb) && !b_addr[12] && (b_src != OWN);
    assign snoop_index  = b_addr[4:2];
    assign snoop_tag    = b_addr[10:5];

    assign inv0 = remote_write && sec0_valid && (((sec0_tag ^ snoop_tag) & TAG_MASK) == 6'b0);
    assign inv1 = remote_write && sec1_valid && (((sec1_tag ^ snoop_tag) & TAG_MASK) == 6'b0);

    assign kill_fill = remote_write && in_fill
                    && (b_addr[RAW-1:2] == miss_addr[RAW-1:2]);

    // Not used: the byte offset, and the address bits above the RAM size
    // (b_addr[12] is used, as the MMIO select).
    logic unused;
    assign unused = &{1'b0, b_addr[11], b_addr[1:0], miss_addr[12:RAW], miss_addr[1:0]};
endmodule
