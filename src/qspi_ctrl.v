/* Copyright 2023-2024 (c) Michael Bell
   SPDX-License-Identifier: Apache-2.0

   Flash-only, read-only QSPI controller for the authQV TT die.

   Stripped from the original tinyqv qspi_controller:
     - PSRAM (RAM A / RAM B) support removed
     - Write support removed
     - Latency configuration straps removed (hardcoded)
     - Formal block removed

   Flash is expected to be in fast read quad I/O (EBh) continuous read mode.
   To start a read: set addr_in and pulse start_read for one cycle.
   Bytes appear on data_out when data_ready is high. Use stall_txn to
   pause between bytes; stop_txn to cancel.
*/
`default_nettype none

module qspi_controller (
    input  wire       clk,
    input  wire       rstn,

    // External SPI interface
    input  wire [3:0] spi_data_in,
    output reg  [3:0] spi_data_out,
    output reg  [3:0] spi_data_oe,
    output wire       spi_clk_out,

    output reg        spi_flash_select,

    // Internal interface for reading data (flash only)
    input  wire [23:0] addr_in,
    input  wire        start_read,
    input  wire        stall_txn,
    input  wire        stop_txn,

    output wire  [7:0] data_out,
    output reg         data_ready,
    output wire        busy
);

    localparam ADDR_BITS       = 24;
    localparam DATA_WIDTH_BITS = 8;

    localparam FSM_IDLE          = 0;
    localparam FSM_ADDR          = 1;
    localparam FSM_DUMMY1        = 2;
    localparam FSM_DUMMY2        = 3;
    localparam FSM_DATA          = 4;
    localparam FSM_STALLED       = 5;
    localparam FSM_STALL_RECOVER = 6;

    // Hardcoded latency: 1 extra read delay cycle (matches strap value 0xC5
    // that the original config used). spi_clk_use_neg is hardcoded to 0.
    localparam [1:0] DELAY_CYCLES_CFG = 2'b01;

    reg  [2:0] fsm_state;
    reg [ADDR_BITS-1:0]       addr;
    reg [DATA_WIDTH_BITS-1:0] data;
    reg  [2:0] nibbles_remaining;
    reg        spi_clk_pos;
    reg  [3:0] spi_in_buffer;

    assign data_out = data;
    assign busy     = (fsm_state != FSM_IDLE);

    reg stop_txn_reg;
    wire stop_txn_now = stop_txn_reg || stop_txn;
    always @(posedge clk) begin
        if (!rstn) stop_txn_reg <= 1'b0;
        else       stop_txn_reg <= stop_txn && !stop_txn_now;
    end

    reg [1:0] read_cycles_count;

/* verilator lint_off WIDTH */
    always @(posedge clk) begin
        if (!rstn || stop_txn_now) begin
            fsm_state         <= FSM_IDLE;
            nibbles_remaining <= 0;
            data_ready        <= 1'b0;
            spi_clk_pos       <= 1'b0;
            spi_data_oe       <= 4'b0000;
            spi_flash_select  <= 1'b1;
            read_cycles_count <= 2'b00;
        end else begin
            data_ready <= 1'b0;

            if (fsm_state == FSM_IDLE) begin
                if (start_read) begin
                    fsm_state         <= FSM_ADDR;
                    nibbles_remaining <= 6 - 1;          // 24-bit address
                    spi_data_oe       <= 4'b1111;
                    spi_clk_pos       <= 1'b0;
                    spi_flash_select  <= 1'b0;
                end
            end else begin
                if (read_cycles_count == 0) read_cycles_count <= 2'b01;
                else                        read_cycles_count <= read_cycles_count - 2'b01;

                if (fsm_state == FSM_STALLED) begin
                    spi_clk_pos <= 1'b0;
                    if (!stall_txn && !read_cycles_count[1]) begin
                        data_ready        <= 1'b1;
                        fsm_state         <= (DELAY_CYCLES_CFG[1] == 0)
                                              ? FSM_DATA : FSM_STALL_RECOVER;
                        read_cycles_count <= {1'b0, DELAY_CYCLES_CFG[0]};
                    end
                end else begin
                    spi_clk_pos <= !spi_clk_pos;

                    if ((fsm_state == FSM_DATA || fsm_state == FSM_STALL_RECOVER)
                            ? (read_cycles_count == 0) : spi_clk_pos) begin
                        if (nibbles_remaining == 0) begin
                            if (fsm_state == FSM_DATA || fsm_state == FSM_STALL_RECOVER) begin
                                data_ready        <= !stall_txn;
                                nibbles_remaining <= (DATA_WIDTH_BITS >> 2) - 1;
                                if (stall_txn) begin
                                    fsm_state         <= FSM_STALLED;
                                    read_cycles_count <= DELAY_CYCLES_CFG | 2'b01;
                                end else begin
                                    fsm_state <= FSM_DATA;
                                end
                            end else begin
                                fsm_state <= fsm_state + 1;
                                if (fsm_state == FSM_ADDR) begin
                                    nibbles_remaining <= 2 - 1;     // DUMMY1: 2 nibbles
                                end
                                else if (fsm_state == FSM_DUMMY1) begin
                                    spi_data_oe       <= 4'b0000;
                                    nibbles_remaining <= 4 - 1;     // DUMMY2: 4 nibbles
                                end
                                else if (fsm_state == FSM_DUMMY2) begin
                                    nibbles_remaining <= (DATA_WIDTH_BITS >> 2) - 1;
                                    read_cycles_count <= DELAY_CYCLES_CFG;
                                end
                            end
                        end else begin
                            if (fsm_state == FSM_STALL_RECOVER) fsm_state <= FSM_DATA;
                            nibbles_remaining <= nibbles_remaining - 1;
                        end
                    end
                end
            end
        end
    end
/* verilator lint_on WIDTH */

    // Address shift register
    always @(posedge clk) begin
        if (fsm_state == FSM_IDLE && start_read) begin
            addr <= addr_in[23:0];
        end else if (fsm_state == FSM_ADDR && spi_clk_pos) begin
            addr <= {addr[ADDR_BITS-5:0], 4'b0000};
        end
    end

    // Read data shift register
    always @(posedge clk) begin
        if (read_cycles_count == 0 && fsm_state == FSM_DATA) begin
            data <= {data[DATA_WIDTH_BITS-5:0], spi_data_in};
        end else if (read_cycles_count == 0 && fsm_state == FSM_STALL_RECOVER) begin
            data <= {data[DATA_WIDTH_BITS-5:0], spi_in_buffer};
        end else if (read_cycles_count == 2'b10 && fsm_state == FSM_STALLED) begin
            spi_in_buffer <= spi_data_in;
        end
    end

    // SPI command/address/dummy nibble generation
    always @(*) begin
        case (fsm_state)
            FSM_ADDR:   spi_data_out = addr[ADDR_BITS-1:ADDR_BITS-4];
            FSM_DUMMY1: spi_data_out = 4'b1010;     // continuous read mode bits
            FSM_DATA:   spi_data_out = 4'b1111;     // hi-Z (oe=0), value irrelevant
            default:    spi_data_out = 4'b1010;
        endcase
    end

    // SPI clock output (negedge variant removed)
    assign spi_clk_out = spi_clk_pos;

endmodule
