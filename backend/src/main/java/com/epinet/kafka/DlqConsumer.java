package com.epinet.kafka;

import com.epinet.model.AnalysisRun;
import com.epinet.repository.AnalysisRunRepository;
import org.apache.kafka.clients.consumer.ConsumerRecord;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;

import java.time.LocalDateTime;
import java.util.Optional;

/**
 * WHY THIS CLASS EXISTS:
 * When a message on "analysis.completed" fails consumer processing 3 times,
 * Spring Kafka's DefaultErrorHandler forwards it to "analysis.completed.DLT".
 * Without this consumer, the job would stay stuck as PENDING forever and the
 * frontend would poll until its 5-minute timeout.
 *
 * This consumer extracts the analysisId from the message key and marks the
 * job as FAILED so the user sees an error instead of an infinite spinner.
 */
@Component
@ConditionalOnProperty(name = "analysis.mode", havingValue = "kafka", matchIfMissing = true)
public class DlqConsumer {

    private static final Logger log = LoggerFactory.getLogger(DlqConsumer.class);

    private final AnalysisRunRepository repo;

    public DlqConsumer(AnalysisRunRepository repo) {
        this.repo = repo;
    }

    @KafkaListener(topics = "analysis.completed.DLT", groupId = "epinet-backend-dlq")
    public void onDeadLetter(ConsumerRecord<String, ?> record) {
        String analysisId = record.key();
        log.error("Dead-letter received for analysisId={} from partition={} offset={}",
                analysisId, record.partition(), record.offset());

        if (analysisId == null) {
            log.error("DLT message has no key (analysisId); cannot mark job as FAILED.");
            return;
        }

        Optional<AnalysisRun> optRun = repo.findById(analysisId);
        if (optRun.isEmpty()) {
            log.warn("DLT: no job found for analysisId={}", analysisId);
            return;
        }

        AnalysisRun run = optRun.get();

        // Idempotency: don't overwrite a terminal state
        if (run.getStatus() == AnalysisRun.Status.COMPLETED
                || run.getStatus() == AnalysisRun.Status.FAILED) {
            log.info("DLT: job {} already in terminal state {}, ignoring", analysisId, run.getStatus());
            return;
        }

        run.setStatus(AnalysisRun.Status.FAILED);
        run.setErrorMessage("Message processing failed after retries (dead-letter)");
        run.setCompletedAt(LocalDateTime.now());
        run.setCsvData(null);
        repo.save(run);
        log.warn("DLT: marked job {} as FAILED", analysisId);
    }
}
