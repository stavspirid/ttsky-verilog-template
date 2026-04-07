/*
 * Copyright (c) 2024 Michael Bell
 * SPDX-License-Identifier: Apache-2.0
 */

`default_nettype none

module tt_um_authQV (
    input  wire [7:0] ui_in,    // Dedicated inputs
    output wire [7:0] uo_out,   // Dedicated outputs
/*verilator lint_off UNUSEDSIGNAL*/
    input  wire [7:0] uio_in,   // IOs: Input path - only some bits used
/*verilator lint_on UNUSEDSIGNAL*/
    output wire [7:0] uio_out,  // IOs: Output path
    output wire [7:0] uio_oe,   // IOs: Enable path (active high: 0=input, 1=output)
/*verilator lint_off UNUSEDSIGNAL*/
    input  wire       ena,
/*verilator lint_on UNUSEDSIGNAL*/
    input  wire       clk,
    input  wire       rst_n
);

    // Address to peripheral map
    localparam PERI_NONE     = 4'hF;
    localparam PERI_GPIO_OUT = 4'h0;
    localparam PERI_GPIO_IN  = 4'h1;

    // Register the reset on the negative edge of clock for safety.
    /* verilator lint_off SYNCASYNCNET */
    reg rst_reg_n;
    /* verilator lint_on SYNCASYNCNET */
    always @(negedge clk) rst_reg_n <= rst_n;

    // Bidirs are used for SPI interface
    wire [3:0] qspi_data_in = {uio_in[5:4], uio_in[2:1]};
    wire [3:0] qspi_data_out;
    wire [3:0] qspi_data_oe;
    wire       qspi_clk_out;
    wire       qspi_flash_select;
    wire       qspi_ram_a_select;
    wire       qspi_ram_b_select;
    assign uio_out = {qspi_ram_b_select, qspi_ram_a_select, qspi_data_out[3:2],
                      qspi_clk_out, qspi_data_out[1:0], qspi_flash_select};
    assign uio_oe = rst_n ? {2'b11, qspi_data_oe[3:2], 1'b1, qspi_data_oe[1:0], 1'b1} : 8'h00;

    wire [27:0] addr;
    wire  [1:0] write_n;
    wire  [1:0] read_n;
/*verilator lint_off UNUSEDSIGNAL*/
    wire        read_complete;
    wire [31:0] data_to_write;  // Currently only bottom byte used.
/*verilator lint_on UNUSEDSIGNAL*/

    wire        data_ready;
    reg [31:0] data_from_read;

    // Peripheral IOs on ui_in and uo_out
    reg  [7:0] gpio_out;

    // All transactions to peripherals complete immediately
    assign data_ready = 1'b1;
    reg [3:0] connect_peripheral;

    tinyQV i_tinyqv(
        .clk(clk),
        .rstn(rst_reg_n),

        .data_addr(addr),
        .data_write_n(write_n),
        .data_read_n(read_n),
        .data_read_complete(read_complete),
        .data_out(data_to_write),

        .data_ready(data_ready),
        .data_in(data_from_read),

        .spi_data_in(qspi_data_in),
        .spi_data_out(qspi_data_out),
        .spi_data_oe(qspi_data_oe),
        .spi_clk_out(qspi_clk_out),
        .spi_flash_select(qspi_flash_select),
        .spi_ram_a_select(qspi_ram_a_select),
        .spi_ram_b_select(qspi_ram_b_select)
    );

    assign uo_out = gpio_out;

    always @(*) begin
        if ({addr[27:6], addr[1:0]} == 24'h800000)
            connect_peripheral = addr[5:2];
        else
            connect_peripheral = PERI_NONE;
    end

    // Read data
    always @(*) begin
        case (connect_peripheral)
            PERI_GPIO_OUT: data_from_read = {24'h0, gpio_out};
            PERI_GPIO_IN:  data_from_read = {24'h0, ui_in};
            default:       data_from_read = 32'hFFFF_FFFF;
        endcase
    end

    // GPIO Out
    always @(posedge clk) begin
        if (!rst_reg_n) begin
            gpio_out <= 0;
        end else if (write_n != 2'b11) begin
            if (connect_peripheral == PERI_GPIO_OUT) gpio_out <= data_to_write[7:0];
        end
    end

endmodule
