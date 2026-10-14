package com.epinet.kafka;

import com.epinet.model.AnalysisRun;
import com.epinet.repository.AnalysisRunRepository;
import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.Map;
import java.util.Optional;

/**
 * WHY THIS CLASS EXISTS:
 * Consumes events from the "analysis.completed" topic, which the Python ML
 * service publishes after finishing (or failing) an analysis. Updates the
 * corresponding job row in MySQL to COMPLETED or FAILED.
 *
 * IDEMPOTENCY: If a job is already COMPLETED or FAILED (e.g., due to a
 * duplicate Kafka delivery), the consumer silently ignores the event.
 * This prevents stale re-deliveries from overwriting final results.
 *
 * POLYGLOT DESERIALIZATION:
 * The Python ML service sends JSON without Java-specific __TypeId__ headers.
 * Consuming as String and parsing with Jackson ObjectMapper prevents
 * Kafka deserializer crashes across languages.
 */
@Component
@ConditionalOnProperty(name = "analysis.mode", havingValue = "kafka", matchIfMissing = true)
public class AnalysisCompletionConsumer {

    private static final Logger log = LoggerFactory.getLogger(AnalysisCompletionConsumer.class);

    private final AnalysisRunRepository repo;
    private final ObjectMapper mapper;

    public AnalysisCompletionConsumer(AnalysisRunRepository repo, ObjectMapper mapper) {
        this.repo = repo;
        this.mapper = mapper;
    }

    @KafkaListener(topics = "analysis.completed", groupId = "epinet-backend")
    public void onAnalysisCompleted(String eventJson) {
        Map<String, Object> event;
        try {
            event = mapper.readValue(eventJson, new TypeReference<>() {});
        } catch (Exception e) {
            log.error("Failed to parse analysis.completed event JSON: {}", eventJson, e);
            return;
        }

        String analysisId = (String) event.get("analysisId");
        String status = (String) event.get("status");

        if (analysisId == null || status == null) {
            log.warn("Received analysis.completed event with missing fields: {}", event);
            return;
        }

        Optional<AnalysisRun> optRun = repo.findById(analysisId);
        if (optRun.isEmpty()) {
            log.warn("Received completion for unknown analysisId={}", analysisId);
            return;
        }

        AnalysisRun run = optRun.get();

        // ── Idempotency guard ───────────────────────────────────────────
        if (run.getStatus() == AnalysisRun.Status.COMPLETED
                || run.getStatus() == AnalysisRun.Status.FAILED) {
            log.info("Job {} is already in terminal state {}, ignoring completed event",
                    analysisId, run.getStatus());
            return;
        }

        LocalDateTime now = LocalDateTime.now();
        run.setCompletedAt(now);

        if ("COMPLETED".equalsIgnoreCase(status)) {
            String resultJson = (String) event.get("resultJson");
            run.setStatus(AnalysisRun.Status.COMPLETED);
            run.setResultJson(resultJson);
            run.setErrorMessage(null);
            run.setCsvData(null); // free up blob memory once job is complete
            repo.saveAndFlush(run);
            log.info("Job {} marked COMPLETED via Kafka event", analysisId);

        } else if ("FAILED".equalsIgnoreCase(status)) {
            String errorMessage = (String) event.get("errorMessage");
            run.setStatus(AnalysisRun.Status.FAILED);
            run.setErrorMessage(errorMessage != null ? errorMessage : "Analysis failed");
            run.setCsvData(null);
            repo.saveAndFlush(run);
            log.warn("Job {} marked FAILED via Kafka event: {}", analysisId, errorMessage);

        } else {
            log.warn("Unknown status '{}' in analysis.completed for job {}", status, analysisId);
        }
    }
}
