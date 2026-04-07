/*
 * Copyright (c) 2024
 * SPDX-License-Identifier: Apache-2.0
 *
 * TT chip top-level. The CPU lives on the FPGA; this die contains
 * only the QSPI memory controller and a 16-phase parallel-bus bridge
 * that exposes the mem_ctrl interface to the FPGA over ui_in/uo_out.
 */

`default_nettype none

module tt_um_authQV (
    input  wire [7:0] ui_in,    // Parallel bus FROM FPGA
    output wire [7:0] uo_out,   // Parallel bus TO FPGA
/*verilator lint_off UNUSEDSIGNAL*/
    input  wire [7:0] uio_in,   // QSPI data in (only some bits used)
/*verilator lint_on UNUSEDSIGNAL*/
    output wire [7:0] uio_out,  // QSPI data out + CS + SCK
    output wire [7:0] uio_oe,   // QSPI output enables
/*verilator lint_off UNUSEDSIGNAL*/
    input  wire       ena,
/*verilator lint_on UNUSEDSIGNAL*/
    input  wire       clk,
    input  wire       rst_n
);

    // Negedge-registered reset (matches the original tinyqv convention,
    // and ensures qspi_ctrl latches its config straps deterministically).
    /* verilator lint_off SYNCASYNCNET */
    reg rst_reg_n;
    /* verilator lint_on SYNCASYNCNET */
    always @(negedge clk) rst_reg_n <= rst_n;

    // ---- QSPI pin mapping (unchanged) ----
    wire [3:0] qspi_data_in = {uio_in[5:4], uio_in[2:1]};
    wire [3:0] qspi_data_out;
    wire [3:0] qspi_data_oe;
    wire       qspi_clk_out;
    wire       qspi_flash_select;
    wire       qspi_ram_a_select;
    wire       qspi_ram_b_select;

    assign uio_out = {qspi_ram_b_select, qspi_ram_a_select,
                      qspi_data_out[3:2], qspi_clk_out,
                      qspi_data_out[1:0], qspi_flash_select};
    assign uio_oe  = rst_n ? {2'b11, qspi_data_oe[3:2], 1'b1,
                              qspi_data_oe[1:0], 1'b1} : 8'h00;

    // ---- Bridge ↔ memory controller wires ----
    wire [23:1] instr_addr;
    wire        instr_fetch_restart;
    wire        instr_fetch_stall;
    wire        instr_fetch_started;
    wire        instr_fetch_stopped;
    wire [15:0] instr_data;
    wire        instr_ready;

    wire [24:0] data_addr;
    wire  [1:0] data_write_n;
    wire  [1:0] data_read_n;
    wire [31:0] data_to_write;
    wire        data_continue;
    wire        data_ready;
    wire [31:0] data_from_read;

    tt_mem_bridge i_bridge (
        .clk                (clk),
        .rstn               (rst_reg_n),
        .from_fpga          (ui_in),
        .to_fpga            (uo_out),

        .instr_addr         (instr_addr),
        .instr_fetch_restart(instr_fetch_restart),
        .instr_fetch_stall  (instr_fetch_stall),
        .instr_fetch_started(instr_fetch_started),
        .instr_fetch_stopped(instr_fetch_stopped),
        .instr_data         (instr_data),
        .instr_ready        (instr_ready),

        .data_addr          (data_addr),
        .data_write_n       (data_write_n),
        .data_read_n        (data_read_n),
        .data_to_write      (data_to_write),
        .data_continue      (data_continue),
        .data_ready         (data_ready),
        .data_from_read     (data_from_read)
    );

    tinyqv_mem_ctrl i_mem (
        .clk                (clk),
        .rstn               (rst_reg_n),

        .instr_addr         (instr_addr),
        .instr_fetch_restart(instr_fetch_restart),
        .instr_fetch_stall  (instr_fetch_stall),
        .instr_fetch_started(instr_fetch_started),
        .instr_fetch_stopped(instr_fetch_stopped),
        .instr_data         (instr_data),
        .instr_ready        (instr_ready),

        .data_addr          (data_addr),
        .data_write_n       (data_write_n),
        .data_read_n        (data_read_n),
        .data_to_write      (data_to_write),
        .data_continue      (data_continue),
        .data_ready         (data_ready),
        .data_from_read     (data_from_read),

        .spi_data_in        (qspi_data_in),
        .spi_data_out       (qspi_data_out),
        .spi_data_oe        (qspi_data_oe),
        .spi_clk_out        (qspi_clk_out),
        .spi_flash_select   (qspi_flash_select),
        .spi_ram_a_select   (qspi_ram_a_select),
        .spi_ram_b_select   (qspi_ram_b_select)
    );

endmodule
