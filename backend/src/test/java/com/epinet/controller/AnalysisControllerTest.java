package com.epinet.controller;

import com.epinet.repository.AnalysisRunRepository;
import com.epinet.service.AnalysisService;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.web.servlet.MockMvc;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

/**
 * MockMvc integration tests for the upload controller.
 * AnalysisService is mocked so no real ML call is made.
 */
@SpringBootTest
@AutoConfigureMockMvc
class AnalysisControllerTest {

    @Autowired
    private MockMvc mockMvc;

    @Autowired
    private AnalysisRunRepository repo;

    @MockBean
    private AnalysisService analysisService;  // prevent real HTTP call to ML service

    private static final String VALID_CSV_HEADER =
            "sample_id,CYP2D6,CYP2C19,CYP3A4,CYP2C9,VKORC1,SLCO1B1,TPMT,DPYD,UGT1A1,ABCB1,phenotype\n";

    private static final String VALID_CSV_ROW =
            "S001,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,1.0,1.5\n";

    // ── Valid upload returns 202 ──────────────────────────────────────────────

    @Test
    void validUpload_returns202() throws Exception {
        byte[] csv = (VALID_CSV_HEADER + VALID_CSV_ROW).getBytes();
        MockMultipartFile file = new MockMultipartFile(
                "file", "sample.csv", "text/csv", csv);

        mockMvc.perform(multipart("/api/uploads").file(file))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.analysisId").exists())
                .andExpect(jsonPath("$.status").value("PROCESSING"));
    }

    @Test
    void validUploadWithUtf8Bom_returns202() throws Exception {
        // Excel UTF-8 BOM byte sequence: EF BB BF (\uFEFF)
        byte[] bom = new byte[]{(byte) 0xEF, (byte) 0xBB, (byte) 0xBF};
        byte[] csv = (VALID_CSV_HEADER + VALID_CSV_ROW).getBytes(java.nio.charset.StandardCharsets.UTF_8);
        byte[] withBom = new byte[bom.length + csv.length];
        System.arraycopy(bom, 0, withBom, 0, bom.length);
        System.arraycopy(csv, 0, withBom, bom.length, csv.length);

        MockMultipartFile file = new MockMultipartFile(
                "file", "sample_excel.csv", "text/csv", withBom);

        mockMvc.perform(multipart("/api/uploads").file(file))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.analysisId").exists())
                .andExpect(jsonPath("$.status").value("PROCESSING"));
    }

    // ── Validation: wrong extension → 400 ────────────────────────────────────

    @Test
    void wrongExtension_returns400() throws Exception {
        MockMultipartFile file = new MockMultipartFile(
                "file", "data.txt", "text/plain", "col1,col2\n1,2\n".getBytes());

        mockMvc.perform(multipart("/api/uploads").file(file))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").exists());
    }

    // ── Validation: missing sample_id → 400 ──────────────────────────────────

    @Test
    void missingSampleId_returns400() throws Exception {
        String badCsv = "CYP2D6,CYP2C19,CYP3A4,CYP2C9,VKORC1,SLCO1B1,TPMT,DPYD,UGT1A1,ABCB1,phenotype\n"
                + "0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,1.0,1.5\n";
        MockMultipartFile file = new MockMultipartFile(
                "file", "bad.csv", "text/csv", badCsv.getBytes());

        mockMvc.perform(multipart("/api/uploads").file(file))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value(org.hamcrest.Matchers.containsString("sample_id")));
    }

    // ── Validation: missing gene column → 400 ────────────────────────────────

    @Test
    void missingGeneColumn_returns400() throws Exception {
        // omit CYP2D6
        String badCsv = "sample_id,CYP2C19,CYP3A4,CYP2C9,VKORC1,SLCO1B1,TPMT,DPYD,UGT1A1,ABCB1,phenotype\n"
                + "S001,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,1.0,1.5\n";
        MockMultipartFile file = new MockMultipartFile(
                "file", "bad.csv", "text/csv", badCsv.getBytes());

        mockMvc.perform(multipart("/api/uploads").file(file))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value(org.hamcrest.Matchers.containsString("CYP2D6")));
    }

    // ── Validation: missing phenotype → 400 ──────────────────────────────────

    @Test
    void missingPhenotype_returns400() throws Exception {
        String badCsv = "sample_id,CYP2D6,CYP2C19,CYP3A4,CYP2C9,VKORC1,SLCO1B1,TPMT,DPYD,UGT1A1,ABCB1\n"
                + "S001,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,1.0\n";
        MockMultipartFile file = new MockMultipartFile(
                "file", "bad.csv", "text/csv", badCsv.getBytes());

        mockMvc.perform(multipart("/api/uploads").file(file))
                .andExpect(status().isBadRequest())
                .andExpect(jsonPath("$.error").value(org.hamcrest.Matchers.containsString("phenotype")));
    }

    // ── Status: unknown id → 404 ──────────────────────────────────────────────

    @Test
    void unknownId_status404() throws Exception {
        mockMvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders
                .get("/api/analyses/nonexistent-id/status"))
                .andExpect(status().isNotFound());
    }

    // ── History endpoint returns list ─────────────────────────────────────────

    @Test
    void historyEndpoint_returnsList() throws Exception {
        mockMvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders
                .get("/api/analyses"))
                .andExpect(status().isOk())
                .andExpect(content().contentType(MediaType.APPLICATION_JSON));
    }
}
