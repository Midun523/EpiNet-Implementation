package com.epinet.controller;

import com.epinet.dto.AnalysisSummary;
import com.epinet.dto.StatusResponse;
import com.epinet.kafka.AnalysisEventPublisher;
import com.epinet.model.AnalysisRun;
import com.epinet.repository.AnalysisRunRepository;
import com.epinet.service.AnalysisService;
import com.epinet.service.CsvValidator;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.multipart.MultipartFile;

import java.io.IOException;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.stream.Collectors;

/**
 * REST controller exposing the analysis endpoints.
 * CORS is globally configured in {@link com.epinet.config.CorsConfig}.
 *
 * WHY TWO PATHS:
 * The upload endpoint checks analysis.mode to decide between:
 *   - "kafka": save PENDING row → publish event → return 202 (async, event-driven)
 *   - "sync":  save UPLOADED row → call ML service via HTTP → return 202 (async, direct)
 * Both paths are kept so Phase 5 can benchmark sync vs Kafka side-by-side.
 */
@RestController
@RequestMapping("/api")
public class AnalysisController {

    private static final Logger log = LoggerFactory.getLogger(AnalysisController.class);

    private final AnalysisRunRepository repo;
    private final AnalysisService analysisService;
    private final ObjectMapper mapper;
    private final String analysisMode;

    // nullable: only injected when analysis.mode=kafka
    private final AnalysisEventPublisher eventPublisher;

    public AnalysisController(
            AnalysisRunRepository repo,
            AnalysisService analysisService,
            ObjectMapper mapper,
            @Value("${analysis.mode:kafka}") String analysisMode,
            // WHY Optional-style injection: when analysis.mode=sync, the
            // AnalysisEventPublisher bean doesn't exist (@ConditionalOnProperty).
            // Spring's required=false prevents a startup failure.
            @org.springframework.beans.factory.annotation.Autowired(required = false)
            AnalysisEventPublisher eventPublisher
    ) {
        this.repo = repo;
        this.analysisService = analysisService;
        this.mapper = mapper;
        this.analysisMode = analysisMode;
        this.eventPublisher = eventPublisher;
    }

    // ── POST /api/uploads ────────────────────────────────────────────────────

    @PostMapping("/uploads")
    public ResponseEntity<?> upload(@RequestParam("file") MultipartFile file) throws IOException {
        long tUploadStart = System.currentTimeMillis();

        // ── Sub-stage: multipart received ─────────────────────────────
        long tMultipartDone = System.currentTimeMillis();

        // Validate CSV structure and encoding; throws ValidationException on failure
        byte[] bytes = CsvValidator.validate(file);
        long tValidateDone = System.currentTimeMillis();

        String analysisId = UUID.randomUUID().toString();

        if ("kafka".equalsIgnoreCase(analysisMode)) {
            // ── Kafka path ──────────────────────────────────────────────
            // 1. Save PENDING row WITH csv data.
            //    WHY saveAndFlush: guarantees the INSERT statement is flushed to MySQL
            //    before the Kafka event is published, ensuring the ML worker never receives
            //    an event for a record that doesn't yet exist in the DB.
            AnalysisRun run = new AnalysisRun(analysisId, file.getOriginalFilename());
            run.setStatus(AnalysisRun.Status.PENDING);
            run.setCsvData(bytes);
            long tEntityBuilt = System.currentTimeMillis();

            repo.saveAndFlush(run);
            long tSaveDone = System.currentTimeMillis();

            eventPublisher.publishRequested(analysisId, file.getOriginalFilename());
            long tPubDone = System.currentTimeMillis();

            // WHY sub-stage logging: isolates which sub-operation within the upload
            // path contributes to the 6-52s regression seen in Phase 2 benchmarks.
            log.info("STAGE TIMING: Job {} upload_received -> event_published total={} ms "
                    + "(multipart_recv={} ms, csv_validate={} ms, entity_build={} ms, "
                    + "db_insert={} ms, kafka_publish={} ms, csv_size={} bytes)",
                    analysisId,
                    (tPubDone - tUploadStart),
                    (tMultipartDone - tUploadStart),
                    (tValidateDone - tMultipartDone),
                    (tEntityBuilt - tValidateDone),
                    (tSaveDone - tEntityBuilt),
                    (tPubDone - tSaveDone),
                    bytes.length);

            return ResponseEntity.status(HttpStatus.ACCEPTED)
                    .body(Map.of("analysisId", analysisId, "status", "PENDING"));

        } else {
            // ── Sync path (original @Async + WebClient) ─────────────────
            AnalysisRun run = new AnalysisRun(analysisId, file.getOriginalFilename());
            // keep original UPLOADED status for backward compatibility
            run.setCsvData(bytes);
            repo.saveAndFlush(run);

            log.info("Sync path: saved UPLOADED job {}, firing async HTTP call", analysisId);
            analysisService.runAnalysis(analysisId, bytes, file.getOriginalFilename());

            return ResponseEntity.status(HttpStatus.ACCEPTED)
                    .body(Map.of("analysisId", analysisId, "status", "PROCESSING"));
        }
    }

    // ── GET /api/analyses/{id}/status ────────────────────────────────────────

    @GetMapping("/analyses/{id}/status")
    public ResponseEntity<StatusResponse> getStatus(@PathVariable String id) {
        return repo.findById(id)
                .map(r -> ResponseEntity.ok(new StatusResponse(
                        r.getId(), r.getStatus().name(), r.getFilename(),
                        r.getCreatedAt(), r.getErrorMessage())))
                .orElse(ResponseEntity.notFound().build());
    }

    // ── GET /api/analyses/{id}/result ────────────────────────────────────────

    @GetMapping("/analyses/{id}/result")
    public ResponseEntity<?> getResult(@PathVariable String id) {
        return repo.findById(id)
                .map(r -> {
                    if (r.getStatus() != AnalysisRun.Status.COMPLETED) {
                        return ResponseEntity.status(HttpStatus.NOT_FOUND)
                                .<Object>body(Map.of("error", "Result not ready. Status: " + r.getStatus()));
                    }
                    try {
                        // Return parsed JSON object rather than double-encoded string
                        Map<String, Object> result = mapper.readValue(
                                r.getResultJson(), new TypeReference<>() {});
                        return ResponseEntity.ok(result);
                    } catch (Exception e) {
                        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                                .<Object>body(Map.of("error", "Failed to parse stored result"));
                    }
                })
                .orElse(ResponseEntity.notFound().build());
    }

    // ── GET /api/analyses (history) ──────────────────────────────────────────

    @GetMapping("/analyses")
    public List<AnalysisSummary> listAnalyses() {
        return repo.findAllByOrderByCreatedAtDesc().stream()
                .map(r -> new AnalysisSummary(
                        r.getId(), r.getFilename(), r.getStatus().name(), r.getCreatedAt()))
                .collect(Collectors.toList());
    }

    // ── GET /api/internal/analyses/{id}/data ─────────────────────────────────
    // WHY: The Kafka message only carries analysisId (not the CSV data, which
    // could exceed Kafka's 1 MB limit). The ML service calls this endpoint to
    // fetch the CSV bytes stored in MySQL.
    //
    // PENDING -> PROCESSING transition:
    // When the ML worker calls this endpoint to fetch the data, it confirms
    // the worker has picked up the job and started computing. We transition
    // status from PENDING to PROCESSING with a targeted single-column UPDATE,
    // avoiding rewriting the 40 MB csvData blob back to MySQL.

    @GetMapping("/internal/analyses/{id}/data")
    public ResponseEntity<byte[]> getAnalysisData(@PathVariable String id) {
        long tStart = System.currentTimeMillis();
        int updated = repo.updateStatusIf(id, AnalysisRun.Status.PROCESSING, AnalysisRun.Status.PENDING);
        if (updated > 0) {
            log.info("Job {} transitioned PENDING -> PROCESSING upon worker data fetch (status_update took {} ms)",
                    id, (System.currentTimeMillis() - tStart));
        }

        byte[] csvData = repo.findCsvDataById(id);
        if (csvData == null) {
            // Fallback for mock environments (e.g. unit tests that mock findById)
            csvData = repo.findById(id).map(AnalysisRun::getCsvData).orElse(null);
        }
        if (csvData == null || csvData.length == 0) {
            return ResponseEntity.notFound().build();
        }

        return ResponseEntity.ok()
                .contentType(MediaType.APPLICATION_OCTET_STREAM)
                .body(csvData);
    }
}
