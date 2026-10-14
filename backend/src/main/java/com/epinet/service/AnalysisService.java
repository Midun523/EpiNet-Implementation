package com.epinet.service;

import com.epinet.model.AnalysisRun;
import com.epinet.repository.AnalysisRunRepository;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.core.io.ByteArrayResource;
import org.springframework.http.MediaType;
import org.springframework.http.client.MultipartBodyBuilder;
import org.springframework.scheduling.annotation.Async;
import org.springframework.stereotype.Service;
import org.springframework.web.reactive.function.BodyInserters;
import org.springframework.web.reactive.function.client.WebClient;
import org.springframework.web.reactive.function.client.WebClientResponseException;

import java.time.LocalDateTime;

/**
 * Background orchestrator service that invokes the Python ML microservice and persists results.
 * Runs in the custom 'analysisExecutor' thread pool.
 * Note: @Transactional is intentionally omitted from runAnalysis() so DB connections
 * are not held open during long external HTTP calls.
 */
@Service
public class AnalysisService {

    private static final Logger log = LoggerFactory.getLogger(AnalysisService.class);

    private final AnalysisRunRepository repo;
    private final WebClient webClient;
    private final ObjectMapper mapper;

    public AnalysisService(
            AnalysisRunRepository repo,
            ObjectMapper mapper,
            @Value("${ml.service.url:http://localhost:8000}") String mlUrl
    ) {
        this.repo = repo;
        this.mapper = mapper;
        this.webClient = WebClient.builder()
                .baseUrl(mlUrl)
                .codecs(config -> config.defaultCodecs().maxInMemorySize(10 * 1024 * 1024))
                .build();
    }

    /**
     * Runs asynchronously on the 'analysisExecutor' pool after the upload controller responds 202.
     * Transitions status: PROCESSING → COMPLETED | FAILED.
     */
    @Async("analysisExecutor")
    public void runAnalysis(String analysisId, byte[] fileBytes, String filename) {
        log.info("Starting async analysis for id={}", analysisId);

        // Mark as PROCESSING
        AnalysisRun run = repo.findById(analysisId)
                .orElseThrow(() -> new IllegalStateException("Run not found: " + analysisId));
        run.setStatus(AnalysisRun.Status.PROCESSING);
        repo.save(run);

        try {
            // Call ML microservice
            MultipartBodyBuilder bodyBuilder = new MultipartBodyBuilder();
            bodyBuilder.part("file", new ByteArrayResource(fileBytes) {
                @Override public String getFilename() { return filename; }
            }).contentType(MediaType.TEXT_PLAIN);

            String resultJson = webClient.post()
                    .uri("/analyze")
                    .contentType(MediaType.MULTIPART_FORM_DATA)
                    .body(BodyInserters.fromMultipartData(bodyBuilder.build()))
                    .retrieve()
                    .bodyToMono(String.class)
                    .block();

            // Validate that returned payload is parseable JSON
            mapper.readTree(resultJson);

            run.setResultJson(resultJson);
            run.setStatus(AnalysisRun.Status.COMPLETED);
            run.setCompletedAt(LocalDateTime.now());
            log.info("Analysis {} COMPLETED successfully", analysisId);

        } catch (WebClientResponseException ex) {
            String responseBody = ex.getResponseBodyAsString();
            log.error("ML service rejected analysis {}: HTTP {} - {}", analysisId, ex.getStatusCode(), responseBody);

            String detailMessage = "ML service error (" + ex.getStatusCode().value() + ")";
            try {
                JsonNode errNode = mapper.readTree(responseBody);
                if (errNode.has("detail")) {
                    detailMessage = errNode.get("detail").asText();
                } else if (errNode.has("error")) {
                    detailMessage = errNode.get("error").asText();
                }
            } catch (Exception ignored) {
                if (responseBody != null && !responseBody.isBlank()) {
                    detailMessage = responseBody;
                }
            }

            run.setStatus(AnalysisRun.Status.FAILED);
            run.setErrorMessage(detailMessage);
            run.setCompletedAt(LocalDateTime.now());

        } catch (Exception ex) {
            log.error("Analysis {} FAILED with unexpected exception: {}", analysisId, ex.getMessage(), ex);
            run.setStatus(AnalysisRun.Status.FAILED);
            run.setErrorMessage(ex.getMessage() != null ? ex.getMessage() : "Unknown analysis failure");
            run.setCompletedAt(LocalDateTime.now());
        }

        repo.save(run);
    }
}
