`timescale 1ns/1ps
// Exercise the resident monitor entirely through the serial pins.
module os_tb #(
    parameter int NREGS = 16,
    parameter int DCACHE = 0,
    parameter int RAW = 10
);
    localparam int CPB = 64;
    logic clk = 0, rst = 1, uart_rx = 1, uart_tx, done;
    logic dbg_core_run, dbg_lock;
    logic [1:0] dbg_exit;
    always #5 clk = ~clk;
    soc_top #(.NREGS(NREGS), .DCACHE(DCACHE), .RAW(RAW),
              .CLK_FREQ(CPB), .BAUD_RATE(1)) dut (.*);

    task automatic send_byte(input byte b);
        @(negedge clk); uart_rx = 0;
        repeat (CPB) @(negedge clk);
        for (int i = 0; i < 8; i++) begin
            uart_rx = b[i];
            repeat (CPB) @(negedge clk);
        end
        uart_rx = 1;
        repeat (CPB) @(negedge clk);
    endtask
    task automatic send_word(input logic [31:0] w);
        for (int i = 0; i < 4; i++) send_byte(w[i*8 +: 8]);
    endtask
    task automatic send_text(input string s);
        for (int i = 0; i < s.len(); i++) send_byte(s[i]);
    endtask

    string output_text = "";
    initial forever begin
        byte b;
        @(negedge uart_tx);
        repeat (CPB + CPB/2) @(posedge clk);
        for (int i = 0; i < 8; i++) begin
            b[i] = uart_tx;
            repeat (CPB) @(posedge clk);
        end
        output_text = {output_text, string'(b)};
    end

    integer prompts = 0, writes = 0, payload_fetches = 0;
    logic [31:0] result;
    logic loaded = 0;
    always @(posedge clk) begin
        if (loaded && (!dbg_core_run || dut.boot_req || dut.loading))
            $fatal(1, "shell input restarted bootloader");
        if (loaded && dut.c0_retire && dut.c0_retire_pc >= 32'h300)
            $fatal(1, "core 0 executed user instructions");
        if (dut.c1_retire && dut.c1_retire_pc == 32'h300) payload_fetches++;
        if (dut.b_valid && dut.b_src == 2 && dut.b_addr == 13'h3fc && dut.b_wstrb == 15) begin
            result = dut.b_wdata;
            writes++;
        end
    end
    task automatic wait_prompt(input bit expect_error = 0);
        int elapsed;
        string suffix;
        elapsed = 0;
        suffix = "";
        while (elapsed < 100000) begin
            @(negedge clk);
            if (output_text.len() >= 5)
                suffix = output_text.substr(output_text.len()-5, output_text.len()-1);
            if (suffix == "hex> ") begin
                if (expect_error && output_text.substr(0, 4) != "\r\nERR")
                    $fatal(1, "invalid input did not report ERR: %s", output_text);
                prompts++;
                output_text = "";
                return;
            end
            elapsed++;
        end
        $fatal(1, "shell prompt timeout: %s", output_text);
    endtask
    task automatic expect_result(input int value, input int count);
        repeat (3000) @(negedge clk);
        if (writes != count || result != 32'(value))
            $fatal(1, "payload result %h, writes %0d, expected %0d/%0d", result, writes, value, count);
    endtask

    logic [31:0] image [0:255];
    integer fd, count = 0, scanned;
    initial begin
        fd = $fopen("programs/hex/os.hex", "r");
        if (fd == 0) $fatal(1, "build programs/hex/os.hex first");
        while (!$feof(fd)) begin
            scanned = $fscanf(fd, "%h\n", image[count]);
            if (scanned == 1) count++;
        end
        $fclose(fd);
        repeat (10) @(negedge clk); rst = 0;
        send_word(32'(count));
        for (int i = 0; i < count; i++) send_word(image[i]);
        wait_prompt();
        loaded = 1;
        if (!dut.console_en || !dut.core1_hold) $fatal(1, "OS did not claim console");

        send_text("02a00513 3ea02e23\r\n");
        wait_prompt(); expect_result(42, 1);
        // Same addresses, different instructions, with uppercase hex and tabs.
        send_text("02B00513\t3EA02E23\n");
        wait_prompt(); expect_result(43, 2);
        send_text("123\n"); wait_prompt(1); expect_result(43, 2);
        send_text("02c00513 xyz\n"); wait_prompt(1); expect_result(43, 2);
        // 47 words fit, the 48th must reject the whole line.
        for (int i = 0; i < 48; i++) send_text("00000013 ");
        send_text("\n"); wait_prompt(1); expect_result(43, 2);
        send_text("0000006f\n"); wait_prompt();
        if (dut.core1_hold) $fatal(1, "loop was not launched");
        begin
            int before_stop;
            before_stop = payload_fetches;
            repeat (3000) @(negedge clk);
            if (payload_fetches <= before_stop) $fatal(1, "core 1 loop did not run");
        end
        send_text("!"); wait_prompt();
        if (!dut.core1_hold) $fatal(1, "stop did not hold core 1");
        send_text("02d00513 3ea02e23\n");
        wait_prompt(); expect_result(45, 3);
        // A new line without ! must stop a running loop and replace it.
        send_text("0000006f\n"); wait_prompt();
        repeat (3000) @(negedge clk);
        send_text("02e00513 3ea02e23\n");
        wait_prompt(); expect_result(46, 4);
        if (done || dbg_exit[0]) $fatal(1, "OS exited");
        $display("PASS    os NREGS=%0d DCACHE=%0d RAW=%0d", NREGS, DCACHE, RAW);
        $finish;
    end
    initial begin
        repeat (2000000) @(posedge clk);
        $fatal(1, "OS test timeout");
    end
endmodule
