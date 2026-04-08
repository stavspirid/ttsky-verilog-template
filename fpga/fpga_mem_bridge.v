/*
 * fpga_mem_bridge.v — FPGA side of the 8-phase parallel bus.
 *
 * Mirrors tt_mem_bridge exactly. Both phase counters reset together
 * so the field layout stays in lock-step.
 *
 * Drives the slim flash-only interface: instr fetch + read-only data
 * loads. There is no write path on this bus — writes go to FPGA-local
 * block RAM, handled by fpga_top.
 */
`default_nettype none

module fpga_mem_bridge (
    input  wire        clk,
    input  wire        rstn,

    output reg   [7:0] to_tt,         // drives TT chip's ui_in
    input  wire  [7:0] from_tt,       // reads TT chip's uo_out

    // ---- mirrors tinyqv_mem_ctrl, seen from tinyqv_cpu ----
    input  wire [23:1] instr_addr,
    input  wire        instr_fetch_restart,
    input  wire        instr_fetch_stall,
    output reg         instr_fetch_started,
    output reg         instr_fetch_stopped,
    output reg  [15:0] instr_data,
    output reg         instr_ready,

    input  wire [23:0] data_addr,
    input  wire  [1:0] data_read_n,
    output reg         data_ready,
    output reg  [31:0] data_from_read
);

    reg [2:0] phase;
    always @(posedge clk) begin
        if (!rstn) phase <= 3'd0;
        else       phase <= phase + 3'd1;
    end

    // -------------------------------------------------------
    // SEND TO TT — mirrors tt_mem_bridge's receive schedule
    // -------------------------------------------------------
    always @(posedge clk) begin
        case (phase)
            3'd0: to_tt <= {instr_fetch_restart, instr_fetch_stall,
                            instr_addr[23:18]};
            3'd1: to_tt <= instr_addr[17:10];
            3'd2: to_tt <= instr_addr[9:2];
            3'd3: to_tt <= {instr_addr[1], data_addr[23:18], 1'b0};
            3'd4: to_tt <= data_addr[17:10];
            3'd5: to_tt <= data_addr[9:2];
            3'd6: to_tt <= {data_addr[1:0], data_read_n, 4'b0};
            default: to_tt <= 8'h00;
        endcase
    end

    // -------------------------------------------------------
    // RECEIVE FROM TT
    // TT phase N is observed locally at phase N+1 (one-cycle round
    // trip through registered I/Os).
    // -------------------------------------------------------
    reg status_started, status_stopped, status_instr, status_data;

    always @(posedge clk) begin
        instr_ready         <= 1'b0;
        instr_fetch_started <= 1'b0;
        instr_fetch_stopped <= 1'b0;
        data_ready          <= 1'b0;

        case (phase)
            // Phase 0 (TT phase 7): status byte
            3'd0: begin
                status_started <= from_tt[7];
                status_stopped <= from_tt[6];
                status_instr   <= from_tt[5];
                status_data    <= from_tt[4];
            end
            // Phase 1 (TT phase 0): instr_data low byte
            3'd1: instr_data[7:0]  <= from_tt;
            // Phase 2 (TT phase 1): instr_data high byte → assert instr_ready
            3'd2: begin
                instr_data[15:8]    <= from_tt;
                instr_ready         <= status_instr;
                instr_fetch_started <= status_started;
                instr_fetch_stopped <= status_stopped;
            end
            // Phases 3-6 (TT phases 2-5): data_from_read bytes
            3'd3: data_from_read[7:0]   <= from_tt;
            3'd4: data_from_read[15:8]  <= from_tt;
            3'd5: data_from_read[23:16] <= from_tt;
            3'd6: begin
                data_from_read[31:24] <= from_tt;
                data_ready            <= status_data;
            end
            default: ;
        endcase
    end

endmodule
