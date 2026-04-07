/*
 * fpga_top.v
 *
 * FPGA top-level. Holds the full RISC-V CPU plus a 16-phase
 * parallel-bus bridge to the TT chip, which contains only the
 * QSPI memory controller.
 *
 * Both the FPGA reset and the TT chip's rst_n MUST be driven
 * from the same synchronous reset so the two phase counters
 * start aligned.
 */

`default_nettype none

module fpga_top (
    input  wire       clk,
    input  wire       rst_n,

    // Wires to TT chip's dedicated pins
    output wire [7:0] tt_ui_in,   // drives ui_in on TT chip
    input  wire [7:0] tt_uo_out,  // reads uo_out from TT chip

    // Application I/O
    input  wire [7:0] gpio_in,
    output wire [7:0] gpio_out
);

    // ---- Reset synchronization ----
    reg rst_sync;
    always @(posedge clk) rst_sync <= rst_n;

    // ---- CPU ↔ peripheral bus ----
    wire [27:0] data_addr;
    wire  [1:0] data_write_n;
    wire  [1:0] data_read_n;
    wire        data_read_complete;
    wire [31:0] data_out;
    reg  [31:0] data_in;
    wire        data_ready_combined;
    wire        data_continue;

    // ---- CPU ↔ instruction fetch (through bridge) ----
    wire [23:1] instr_addr;
    wire        instr_fetch_restart;
    wire        instr_fetch_stall;
    wire        instr_fetch_started;
    wire        instr_fetch_stopped;
    wire [15:0] instr_data;
    wire        instr_ready;

    // ---- Memory / peripheral split ----
    // is_mem=1: route to TT chip bridge; is_mem=0: local peripherals
    wire is_mem = (data_addr[27:25] == 3'b000);

    wire        mem_data_ready;
    wire [31:0] mem_data_from_read;
    wire  [1:0] mem_data_write_n = is_mem ? data_write_n : 2'b11;
    wire  [1:0] mem_data_read_n  = is_mem ? data_read_n  : 2'b11;

    assign data_ready_combined = is_mem ? mem_data_ready : 1'b1;

    // ---- CPU ----
    tinyqv_cpu #(.NUM_REGS(16), .REG_ADDR_BITS(4)) cpu (
        .clk                (clk),
        .rstn               (rst_sync),
        .instr_addr         (instr_addr),
        .instr_fetch_restart(instr_fetch_restart),
        .instr_fetch_stall  (instr_fetch_stall),
        .instr_fetch_started(instr_fetch_started),
        .instr_fetch_stopped(instr_fetch_stopped),
        .instr_data_in      (instr_data),
        .instr_ready        (instr_ready),
        .data_addr          (data_addr),
        .data_write_n       (data_write_n),
        .data_read_n        (data_read_n),
        .data_read_complete (data_read_complete),
        .data_out           (data_out),
        .data_continue      (data_continue),
        .data_ready         (data_ready_combined),
        .data_in            (data_in)
    );

    // ---- Bridge to TT chip ----
    fpga_mem_bridge i_bridge (
        .clk                (clk),
        .rstn               (rst_sync),
        .to_tt              (tt_ui_in),
        .from_tt            (tt_uo_out),
        .instr_addr         (instr_addr),
        .instr_fetch_restart(instr_fetch_restart),
        .instr_fetch_stall  (instr_fetch_stall),
        .instr_fetch_started(instr_fetch_started),
        .instr_fetch_stopped(instr_fetch_stopped),
        .instr_data         (instr_data),
        .instr_ready        (instr_ready),
        .data_addr          (data_addr[24:0]),
        .data_write_n       (mem_data_write_n),
        .data_read_n        (mem_data_read_n),
        .data_to_write      (data_out),
        .data_continue      (data_continue),
        .data_ready         (mem_data_ready),
        .data_from_read     (mem_data_from_read)
    );

    // ---- Local peripherals: simple GPIO ----
    reg [7:0] gpio_out_r;
    assign gpio_out = gpio_out_r;

    always @(*) begin
        case (data_addr[5:2])
            4'h0:    data_in = {24'h0, gpio_out_r};
            4'h1:    data_in = {24'h0, gpio_in};
            default: data_in = is_mem ? mem_data_from_read : 32'hFFFF_FFFF;
        endcase
    end

    always @(posedge clk) begin
        if (!rst_sync) begin
            gpio_out_r <= 8'd0;
        end else if (!is_mem && data_write_n != 2'b11
                     && data_addr[5:2] == 4'h0) begin
            gpio_out_r <= data_out[7:0];
        end
    end

endmodule
