/* TinyQV: flash-only memory controller for the authQV TT die.
 *
 * Plumbs the CPU's instruction-fetch and data-read interface into the
 * stripped flash-only qspi_controller. Writes and PSRAM are gone — the
 * companion FPGA handles writable memory locally.
 */
`default_nettype none

module tinyqv_mem_ctrl (
    input  wire        clk,
    input  wire        rstn,

    // ---- Instruction fetch ----
    input  wire [23:1] instr_addr,
    input  wire        instr_fetch_restart,
    input  wire        instr_fetch_stall,
    output reg         instr_fetch_started,
    output reg         instr_fetch_stopped,
    output wire [15:0] instr_data,
    output wire        instr_ready,

    // ---- Data read (no writes, no continue) ----
    input  wire [23:0] data_addr,
    input  wire  [1:0] data_read_n,    // 11 = no read, 00/01/10 = 1/2/4 byte
    output wire        data_ready,
    output wire [31:0] data_from_read,

    // ---- External SPI pins ----
    input  wire  [3:0] spi_data_in,
    output wire  [3:0] spi_data_out,
    output wire  [3:0] spi_data_oe,
    output wire        spi_clk_out,
    output wire        spi_flash_select
);

    // Combinational scheduling
    reg  start_instr;
    reg  start_read;
    reg  stop_txn;  // wire that ends any QSPI transaction
    reg  [1:0] data_txn_len;    // Stores the calculated target length for a data read

    wire qspi_busy;
    reg  instr_active;

    // =1 if an instruction is actively being fetched, or if one is about to be scheduled on this exact clock cycle
    wire is_instr      = instr_active || start_instr;
    // If insn -> 2 bytes, else data read -> 1/2/4 bytes
    wire [1:0] txn_len = is_instr ? 2'b01 : data_txn_len;
    // MUX to route correct memory address to flash
    wire [23:0] addr_in = is_instr ? {instr_addr, 1'b0} : data_addr;

    reg  [31:0] qspi_data_buf;
    reg  [1:0]  qspi_data_byte_idx;
    wire        qspi_data_ready;    // =1 for 1cycle when a new byte arrives to qspi cntrl
    wire [7:0]  qspi_data_out;

    // Stall on the last byte of an instruction (for the prefetch buffer)
    wire stall_txn = instr_active && instr_fetch_stall && !instr_ready
                     && qspi_data_byte_idx == 2'b01;

    always @(*) begin
        start_instr  = 1'b0;
        start_read   = 1'b0;
        stop_txn     = 1'b0;
        // Decode read length: 00->1Byte (len=0), 01->2Byte (len=1), 10->4Byte (len=3)
        data_txn_len = {data_read_n[1], data_read_n[1] | data_read_n[0]};

        if (qspi_busy) begin    // Search reasons to stop current transaction
            if (instr_active) begin // if cur insn is IF
                // If CPU requests restart && fetch not started yet or fetch is stalled
                if (instr_fetch_restart && (!instr_fetch_started || stall_txn)) begin
                    stop_txn = 1'b1;
                // If insn finished rcv 2nd byte || CPU requests a stall
                end else if ((qspi_data_ready && qspi_data_byte_idx == 2'b01)
                             || instr_fetch_stall) begin
                    // Checks for pending data read req
                    if (data_read_n != 2'b11) stop_txn = 1'b1;
                end
            // Signal the successful end of txn
            end else if (qspi_data_ready && qspi_data_byte_idx == data_txn_len) begin
                stop_txn = 1'b1;
            end
        end else begin
            // Data reads take priority over IF
            if (data_read_n != 2'b11)         start_read  = 1'b1;
            else if (instr_fetch_restart)     start_instr = 1'b1;
        end
    end

    always @(posedge clk) begin
        if (!rstn || stop_txn) instr_active <= 1'b0;
        else                   instr_active <= qspi_busy ? instr_active : start_instr;
    end

    qspi_controller q_ctrl (
        .clk              (clk),
        .rstn             (rstn),

        .spi_data_in      (spi_data_in),
        .spi_data_out     (spi_data_out),
        .spi_data_oe      (spi_data_oe),
        .spi_clk_out      (spi_clk_out),
        .spi_flash_select (spi_flash_select),

        .addr_in          (addr_in),
        .start_read       (start_read || start_instr),
        .stall_txn        (stall_txn),
        .stop_txn         (stop_txn),   // For both insn fetch or data read

        .data_out         (qspi_data_out),
        .data_ready       (qspi_data_ready),
        .busy             (qspi_busy)
    );

    always @(posedge clk) begin
        if (!rstn) begin
            instr_fetch_started <= 1'b0;
            instr_fetch_stopped <= 1'b0;
        end else begin
            instr_fetch_started <= start_instr;
            instr_fetch_stopped <= stop_txn && instr_active;    // Insn fetch is interrupted
        end
    end

    // Next byte logic signals
    always @(posedge clk) begin
        if (!rstn || start_instr || start_read) begin
            qspi_data_byte_idx <= 2'b00;
        end else if (qspi_data_ready) begin
            qspi_data_byte_idx <= qspi_data_byte_idx + 2'b01;
            if (qspi_data_byte_idx == txn_len) qspi_data_byte_idx <= 2'b00;
        end
    end

    // Place incoming byte inside 32-bit buffer
    always @(posedge clk) begin
        if (qspi_data_ready) begin
            qspi_data_buf[{qspi_data_byte_idx, 3'b000} +: 8] <= qspi_data_out;
        end
    end

    assign instr_data  = {qspi_data_out, qspi_data_buf[7:0]};   // NOTE: why grab live byte here? (qspi_data_out)
    assign instr_ready = instr_active && qspi_data_ready
                         && qspi_data_byte_idx == 2'b01;

    assign data_ready = !instr_active && qspi_data_ready
                        && qspi_data_byte_idx == data_txn_len;
    assign data_from_read = data_ready
        // MUX for deifferent byte lengths
        ? {qspi_data_out,
           qspi_data_buf[23:16],
           data_txn_len == 2'b01 ? qspi_data_out : qspi_data_buf[15:8],
           data_txn_len == 2'b00 ? qspi_data_out : qspi_data_buf[7:0]}
        : qspi_data_buf;

endmodule
