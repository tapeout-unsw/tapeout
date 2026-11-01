`timescale 1ns / 1ps

module kv260_pl_reset_tb;
    reg clk = 1'b0;
    wire resetn;

    kv260_pl_reset dut (
        .clk    (clk),
        .resetn (resetn)
    );

    always #5 clk = ~clk;

    initial begin
        #1;
        if (resetn !== 1'b0)
            $fatal(1, "resetn was not asserted immediately after configuration");

        repeat (31) begin
            @(posedge clk);
            #1;
            if (resetn !== 1'b0)
                $fatal(1, "resetn deasserted before 32 rising clock edges");
        end

        @(posedge clk);
        #1;
        if (resetn !== 1'b1)
            $fatal(1, "resetn did not deassert after 32 rising clock edges");

        repeat (8) begin
            @(posedge clk);
            #1;
            if (resetn !== 1'b1)
                $fatal(1, "resetn asserted again after startup");
        end

        $display("PASS    kv260_pl_reset");
        $finish;
    end
endmodule
