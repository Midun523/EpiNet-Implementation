package com.epinet.kafka;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.stereotype.Component;

/**
 * WHY THIS CLASS EXISTS:
 * Publishes analysis events to Kafka topics. This is the ONLY place that sends
 * messages to Kafka from the backend, keeping the publishing logic in one spot.
 *
 * IMPORTANT: The caller (AnalysisController) must ensure the PENDING job row is
 * already committed to MySQL before calling publishRequested(). This guarantees
 * the ML service won't receive an event for a job that doesn't exist yet.
 *
 * The analysisId is used as the Kafka message key so that all events for the
 * same analysis land on the same partition (preserves ordering per job).
 */
@Component
@ConditionalOnProperty(name = "analysis.mode", havingValue = "kafka", matchIfMissing = true)
public class AnalysisEventPublisher {

    private static final Logger log = LoggerFactory.getLogger(AnalysisEventPublisher.class);

    private final KafkaTemplate<String, Object> kafkaTemplate;

    public AnalysisEventPublisher(KafkaTemplate<String, Object> kafkaTemplate) {
        this.kafkaTemplate = kafkaTemplate;
    }

    /**
     * Publishes an analysis.requested event. Call only AFTER the PENDING row
     * is committed to MySQL.
     */
    public void publishRequested(String analysisId, String filename) {
        AnalysisRequestedEvent event = new AnalysisRequestedEvent(analysisId, filename);
        kafkaTemplate.send("analysis.requested", analysisId, event)
                .whenComplete((result, ex) -> {
                    if (ex != null) {
                        log.error("Failed to publish analysis.requested for {}: {}",
                                analysisId, ex.getMessage());
                    } else {
                        log.info("Published analysis.requested for {} to partition {}",
                                analysisId, result.getRecordMetadata().partition());
                    }
                });
    }
}
