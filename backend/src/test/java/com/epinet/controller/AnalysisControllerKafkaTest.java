package com.epinet.controller;

import com.epinet.kafka.AnalysisEventPublisher;
import com.epinet.model.AnalysisRun;
import com.epinet.repository.AnalysisRunRepository;
import com.epinet.service.AnalysisService;
import org.junit.jupiter.api.Test;
import org.mockito.Mockito;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.mock.web.MockMultipartFile;
import org.springframework.test.context.TestPropertySource;
import org.springframework.test.web.servlet.MockMvc;

import java.util.Optional;

import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.multipart;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.content;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

/**
 * WHY THIS CLASS EXISTS:
 * WebMvc slice test verifying that when analysis.mode=kafka:
 * 1. An uploaded file is persisted with status PENDING and csvData stored in MySQL.
 * 2. An event is published to Kafka via AnalysisEventPublisher.
 * 3. The internal /api/internal/analyses/{id}/data endpoint correctly serves the raw CSV bytes
 *    so the ML worker can fetch it when consuming the Kafka event.
 *
 * Using @WebMvcTest isolates the controller without needing a running Kafka broker or DB.
 */
@WebMvcTest(AnalysisController.class)
@TestPropertySource(properties = "analysis.mode=kafka")
@org.springframework.test.context.ActiveProfiles("test")
class AnalysisControllerKafkaTest {

    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private AnalysisRunRepository repo;

    @MockBean
    private AnalysisService analysisService;

    @MockBean
    private AnalysisEventPublisher eventPublisher;

    private static final String VALID_CSV =
            "sample_id,CYP2D6,CYP2C19,CYP3A4,CYP2C9,VKORC1,SLCO1B1,TPMT,DPYD,UGT1A1,ABCB1,phenotype\n" +
            "S001,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8,0.9,1.0,1.5\n";

    @Test
    void uploadInKafkaMode_savesPendingAndPublishesEvent() throws Exception {
        byte[] csvBytes = VALID_CSV.getBytes();
        MockMultipartFile file = new MockMultipartFile(
                "file", "kafka_test.csv", "text/csv", csvBytes);

        when(repo.save(any(AnalysisRun.class))).thenAnswer(invocation -> invocation.getArgument(0));

        String responseJson = mockMvc.perform(multipart("/api/uploads").file(file))
                .andExpect(status().isAccepted())
                .andExpect(jsonPath("$.status").value("PENDING"))
                .andExpect(jsonPath("$.analysisId").exists())
                .andReturn()
                .getResponse()
                .getContentAsString();

        String analysisId = com.jayway.jsonpath.JsonPath.read(responseJson, "$.analysisId");

        // Verify Kafka publisher was invoked with analysisId and filename
        verify(eventPublisher, Mockito.times(1)).publishRequested(Mockito.eq(analysisId), Mockito.eq("kafka_test.csv"));

        // Set up repo mock for internal data endpoint
        AnalysisRun run = new AnalysisRun(analysisId, "kafka_test.csv");
        run.setCsvData(csvBytes);
        when(repo.findById(analysisId)).thenReturn(Optional.of(run));

        // Verify internal endpoint returns raw CSV bytes
        mockMvc.perform(get("/api/internal/analyses/" + analysisId + "/data"))
                .andExpect(status().isOk())
                .andExpect(content().bytes(csvBytes));
    }
}
