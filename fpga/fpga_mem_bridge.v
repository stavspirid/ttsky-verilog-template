/*
 * fpga_mem_bridge.v
 *
 * FPGA-side counterpart of tt_mem_bridge. Mirrors the 16-phase
 * schedule exactly; both phase counters reset together so the
 * field layout stays in lock-step.
 */

`default_nettype none

module fpga_mem_bridge (
    input  wire        clk,
    input  wire        rstn,

    output reg   [7:0] to_tt,         // drives TT chip's ui_in
    input  wire  [7:0] from_tt,       // reads TT chip's uo_out

    // ---- same interface as tinyqv_mem_ctrl, seen from tinyqv_cpu ----
    input  wire [23:1] instr_addr,
    input  wire        instr_fetch_restart,
    input  wire        instr_fetch_stall,
    output reg         instr_fetch_started,
    output reg         instr_fetch_stopped,
    output reg  [15:0] instr_data,
    output reg         instr_ready,

    input  wire [24:0] data_addr,
    input  wire  [1:0] data_write_n,
    input  wire  [1:0] data_read_n,
    input  wire [31:0] data_to_write,
    input  wire        data_continue,
    output reg         data_ready,
    output reg  [31:0] data_from_read
);

    reg [3:0] phase;
    always @(posedge clk) begin
        if (!rstn) phase <= 4'd0;
        else       phase <= phase + 4'd1;
    end

    // -------------------------------------------------------
    // SEND TO TT — mirrors tt_mem_bridge's receive schedule
    // -------------------------------------------------------
    always @(posedge clk) begin
        case (phase)
            4'd0:  to_tt <= {instr_fetch_restart, instr_fetch_stall,
                             instr_addr[23:18]};
            4'd1:  to_tt <= instr_addr[17:10];
            4'd2:  to_tt <= instr_addr[9:2];
            4'd3:  to_tt <= {instr_addr[1], data_addr[24:18]};
            4'd4:  to_tt <= data_addr[17:10];
            4'd5:  to_tt <= data_addr[9:2];
            4'd6:  to_tt <= {data_addr[1:0], data_write_n,
                             data_read_n, data_continue, 1'b0};
            4'd8:  to_tt <= data_to_write[7:0];
            4'd9:  to_tt <= data_to_write[15:8];
            4'd10: to_tt <= data_to_write[23:16];
            4'd11: to_tt <= data_to_write[31:24];
            default: to_tt <= 8'h00;
        endcase
    end

    // -------------------------------------------------------
    // RECEIVE FROM TT
    // TT phase N is observed locally at phase N+1 (one-cycle
    // round trip through registered I/Os).
    // -------------------------------------------------------
    reg status_started, status_stopped, status_instr, status_data;

    always @(posedge clk) begin
        // Default-deassert single-cycle pulses
        instr_ready         <= 1'b0;
        instr_fetch_started <= 1'b0;
        instr_fetch_stopped <= 1'b0;
        data_ready          <= 1'b0;

        case (phase)
            4'd15: begin
                status_started <= from_tt[7];
                status_stopped <= from_tt[6];
                status_instr   <= from_tt[5];
                status_data    <= from_tt[4];
            end
            4'd0: instr_data[7:0]  <= from_tt;
            4'd1: begin
                instr_data[15:8]    <= from_tt;
                instr_ready         <= status_instr;
                instr_fetch_started <= status_started;
                instr_fetch_stopped <= status_stopped;
            end
            4'd2: data_from_read[7:0]   <= from_tt;
            4'd3: data_from_read[15:8]  <= from_tt;
            4'd4: data_from_read[23:16] <= from_tt;
            4'd5: begin
                data_from_read[31:24] <= from_tt;
                data_ready            <= status_data;
            end
            default: ;
        endcase
    end

endmodule
