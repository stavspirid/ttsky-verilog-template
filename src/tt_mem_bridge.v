/*
 * tt_mem_bridge.v — slim, flash-only bridge.
 *
 * Exposes the stripped tinyqv_mem_ctrl interface to the FPGA over an
 * 8-phase 8-bit parallel bus on ui_in/uo_out. The FPGA runs an identical
 * 8-phase counter; both reset together to stay in lock-step.
 *
 * Request frame (FPGA → TT, 8 phases):
 *   0: {fetch_restart, fetch_stall, instr_addr[23:18]}
 *   1: instr_addr[17:10]
 *   2: instr_addr[9:2]
 *   3: {instr_addr[1], data_addr[23:18], 1'b0}
 *   4: data_addr[17:10]
 *   5: data_addr[9:2]
 *   6: {data_addr[1:0], data_read_n[1:0], 4'b0}
 *   7: spare
 *
 * Response frame (TT → FPGA, 8 phases):
 *   0: {fetch_started, fetch_stopped, instr_ready, data_ready, 4'b0}
 *   1: instr_data[7:0]
 *   2: instr_data[15:8]
 *   3: data_from_read[7:0]
 *   4: data_from_read[15:8]
 *   5: data_from_read[23:16]
 *   6: data_from_read[31:24]
 *   7: 0
 */
`default_nettype none

module tt_mem_bridge (
    input  wire        clk,
    input  wire        rstn,

    input  wire  [7:0] from_fpga,
    output reg   [7:0] to_fpga,

    // ---- tinyqv_mem_ctrl: instruction fetch ----
    output reg  [23:1] instr_addr,          // Holds the reconstructed mem address (PC) pointing to the requested CPU instruction 
    output reg         instr_fetch_restart, // Signals the mem_ctrl to restart an insn fetch sequence
    output reg         instr_fetch_stall,   // Tells the mem_ctrl to temporarily pause the current insn fetch
    input  wire        instr_fetch_started, // Insn fetch started via QSPI but not finished yet
    input  wire        instr_fetch_stopped,
    input  wire [15:0] instr_data,
    input  wire        instr_ready,

    // ---- tinyqv_mem_ctrl: data read ----
    output reg  [23:0] data_addr,   // Holds the reconstructed memory address for a standard CPU data read request
    output reg   [1:0] data_read_n,
    input  wire        data_ready,
    input  wire [31:0] data_from_read
);

    reg [2:0] phase;
    always @(posedge clk) begin
        if (!rstn) phase <= 3'd0;
        else       phase <= phase + 3'd1;
    end

    // -------------------------------------------------------
    // RECEIVE FROM FPGA
    // -------------------------------------------------------
    always @(posedge clk) begin
        if (!rstn) begin
            instr_fetch_restart <= 1'b0;
            instr_fetch_stall   <= 1'b0;
            instr_addr          <= 23'd0;
            data_addr           <= 24'd0;
            data_read_n         <= 2'b11;   // No read
        end else begin
            case (phase)
                // Get control signals and begin IF stage (give PC to mem_ctrl)
                3'd0: begin
                    instr_fetch_restart <= from_fpga[7];
                    instr_fetch_stall   <= from_fpga[6];
                    instr_addr[23:18]   <= from_fpga[5:0];
                end
                3'd1: instr_addr[17:10] <= from_fpga;
                3'd2: instr_addr[9:2]   <= from_fpga;
                // Get last bit of PC and first 6 bits of data read addr
                3'd3: begin
                    instr_addr[1]    <= from_fpga[7];
                    data_addr[23:18] <= from_fpga[6:1];
                end
                3'd4: data_addr[17:10] <= from_fpga;
                3'd5: data_addr[9:2]   <= from_fpga;
                3'd6: begin
                    data_addr[1:0] <= from_fpga[7:6];
                    data_read_n    <= from_fpga[5:4];
                end
                default: ;
            endcase
        end
    end

    // -------------------------------------------------------
    // LATCH SINGLE-CYCLE PULSES from the memory controller
    // -------------------------------------------------------
    // Ensure generated signals or data from mem_ctrl are held stable until 
    // the FPGA has had a chance to read them.
    reg        started_l, stopped_l, instr_ready_l, data_ready_l;
    reg [15:0] instr_data_l;
    reg [31:0] data_from_read_l;

    always @(posedge clk) begin
        if (!rstn) begin
            started_l        <= 1'b0;
            stopped_l        <= 1'b0;
            instr_ready_l    <= 1'b0;
            data_ready_l     <= 1'b0;
            instr_data_l     <= 16'd0;
            data_from_read_l <= 32'd0;
        end else begin
            if (instr_fetch_started) started_l <= 1'b1;
            if (instr_fetch_stopped) stopped_l <= 1'b1;
            if (instr_ready) begin
                instr_ready_l <= 1'b1;
                instr_data_l  <= instr_data;
            end
            if (data_ready) begin
                data_ready_l     <= 1'b1;
                data_from_read_l <= data_from_read;
            end
            // Clear after the status byte has been transmitted
            if (phase == 3'd7) begin
                started_l     <= instr_fetch_started;
                stopped_l     <= instr_fetch_stopped;
                instr_ready_l <= instr_ready;
                data_ready_l  <= data_ready;
                if (instr_ready) instr_data_l     <= instr_data;
                if (data_ready)  data_from_read_l <= data_from_read;
            end
        end
    end

    // -------------------------------------------------------
    // SEND TO FPGA — registered, one byte per phase
    // -------------------------------------------------------
    always @(posedge clk) begin
        case (phase)
            3'd7: to_fpga <= {started_l     | instr_fetch_started,
                              stopped_l     | instr_fetch_stopped,
                              instr_ready_l | instr_ready,
                              data_ready_l  | data_ready,
                              4'b0000};
            3'd0: to_fpga <= instr_data_l[7:0];
            3'd1: to_fpga <= instr_data_l[15:8];
            3'd2: to_fpga <= data_from_read_l[7:0];
            3'd3: to_fpga <= data_from_read_l[15:8];
            3'd4: to_fpga <= data_from_read_l[23:16];
            3'd5: to_fpga <= data_from_read_l[31:24];
            default: to_fpga <= 8'h00;
        endcase
    end

endmodule
