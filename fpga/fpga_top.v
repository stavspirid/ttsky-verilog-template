/*
 * fpga_top.v — companion FPGA top-level for the authQV TT die.
 *
 * Hosts the full RV32I CPU, FPGA-local writable block RAM, GPIO,
 * and the FPGA side of the parallel-bus bridge to the TT die.
 *
 * Memory map (CPU view, byte addresses):
 *   0x0000_0000 .. 0x00FF_FFFF  Flash (read-only, via TT die)
 *   0x0100_0000 .. 0x0100_FFFF  Block RAM (R/W, FPGA-local, 64 KB)
 *   0x8000_0000 ..              Peripherals (GPIO)
 *
 * The TT die's rst_n MUST be driven from the same synchronous reset
 * signal that drives this module so the two phase counters start at 0
 * on the same cycle.
 */
`default_nettype none

module fpga_top (
    input  wire       clk,
    input  wire       rst_n,

    // ---- Wires to TT chip's dedicated pins ----
    output wire [7:0] tt_ui_in,    // drives ui_in on TT chip
    input  wire [7:0] tt_uo_out,   // reads uo_out from TT chip
    output wire       tt_rst_n,    // drive TT chip's rst_n from this

    // ---- Application I/O ----
    input  wire [7:0] gpio_in,
    output wire [7:0] gpio_out
);

    // ---- Reset synchronization ----
    reg rst_sync;
    always @(posedge clk) rst_sync <= rst_n;
    assign tt_rst_n = rst_sync;

    // ---- CPU bus ----
    wire [27:0] data_addr;
    wire  [1:0] data_write_n;
    wire  [1:0] data_read_n;
    wire        data_read_complete;
    wire [31:0] data_out;
    reg  [31:0] data_in;
    wire        data_continue;
    wire        data_ready_combined;

    // ---- CPU instruction fetch (through bridge) ----
    wire [23:1] instr_addr;
    wire        instr_fetch_restart;
    wire        instr_fetch_stall;
    wire        instr_fetch_started;
    wire        instr_fetch_stopped;
    wire [15:0] instr_data;
    wire        instr_ready;

    // ---- Address decode ----
    wire is_flash = (data_addr[27:24] == 4'h0);                  // 0x0000_0000..0x00FF_FFFF
    wire is_ram   = (data_addr[27:16] == 12'h010);               // 0x0100_0000..0x0100_FFFF
    wire is_peri  =  data_addr[27];                              // 0x8000_0000+

    // ---- Bridge wires (flash reads only) ----
    wire        bridge_data_ready;
    wire [31:0] bridge_data_from_read;
    wire  [1:0] bridge_data_read_n = is_flash ? data_read_n : 2'b11;

    // ---- Block RAM (16K x 32-bit = 64 KB) ----
    reg  [31:0] bram [0:16383];
    reg  [31:0] bram_rdata;
    reg         bram_ready;

    wire [13:0] bram_idx = data_addr[15:2];
    wire [1:0]  bram_boff = data_addr[1:0];

    // Byte-enable derived from data_write_n + bram_boff
    reg [3:0] bram_we;
    always @(*) begin
        bram_we = 4'b0000;
        if (is_ram && data_write_n != 2'b11) begin
            case (data_write_n)
                2'b00: bram_we = 4'b0001 << bram_boff;     // SB
                2'b01: bram_we = bram_boff[1] ? 4'b1100 : 4'b0011; // SH
                2'b10: bram_we = 4'b1111;                  // SW
                default: bram_we = 4'b0000;
            endcase
        end
    end

    // Replicate the store data across all byte lanes so any byte enable
    // can pick from data_out[7:0]/[15:8]/etc. The CPU already places the
    // byte in [7:0] for SB and the halfword in [15:0] for SH.
    wire [31:0] bram_wdata = {
        data_write_n == 2'b00 ? data_out[7:0]  :
        data_write_n == 2'b01 ? data_out[7:0]  : data_out[31:24],
        data_write_n == 2'b00 ? data_out[7:0]  :
        data_write_n == 2'b01 ? data_out[15:8] : data_out[23:16],
        data_write_n == 2'b00 ? data_out[7:0]  : data_out[15:8],
        data_out[7:0]
    };

    always @(posedge clk) begin
        if (is_ram && bram_we[0]) bram[bram_idx][7:0]   <= bram_wdata[7:0];
        if (is_ram && bram_we[1]) bram[bram_idx][15:8]  <= bram_wdata[15:8];
        if (is_ram && bram_we[2]) bram[bram_idx][23:16] <= bram_wdata[23:16];
        if (is_ram && bram_we[3]) bram[bram_idx][31:24] <= bram_wdata[31:24];
        bram_rdata <= bram[bram_idx];
        bram_ready <= is_ram && (data_read_n != 2'b11 || data_write_n != 2'b11);
    end

    // ---- Peripherals: simple GPIO ----
    reg [7:0] gpio_out_r;
    assign gpio_out = gpio_out_r;

    always @(posedge clk) begin
        if (!rst_sync) begin
            gpio_out_r <= 8'd0;
        end else if (is_peri && data_write_n != 2'b11
                     && data_addr[5:2] == 4'h0) begin
            gpio_out_r <= data_out[7:0];
        end
    end

    // ---- Read mux ----
    always @(*) begin
        if (is_flash)     data_in = bridge_data_from_read;
        else if (is_ram)  data_in = bram_rdata;
        else if (is_peri) begin
            case (data_addr[5:2])
                4'h0:    data_in = {24'h0, gpio_out_r};
                4'h1:    data_in = {24'h0, gpio_in};
                default: data_in = 32'hFFFF_FFFF;
            endcase
        end else          data_in = 32'hFFFF_FFFF;
    end

    // Flash writes are silently dropped; assert ready immediately so the
    // CPU doesn't hang on a stray store into ROM space.
    wire flash_write_drop = is_flash && data_write_n != 2'b11;

    assign data_ready_combined = is_flash ? (bridge_data_ready | flash_write_drop)
                               : is_ram   ?  bram_ready
                                          :  1'b1;   // peripherals: 1-cycle

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
        .data_addr          (data_addr[23:0]),
        .data_read_n        (bridge_data_read_n),
        .data_ready         (bridge_data_ready),
        .data_from_read     (bridge_data_from_read)
    );

endmodule
