package com.epinet.config;

import org.apache.kafka.clients.admin.AdminClientConfig;
import org.apache.kafka.clients.admin.NewTopic;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.kafka.config.TopicBuilder;
import org.springframework.kafka.core.KafkaAdmin;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.kafka.listener.DeadLetterPublishingRecoverer;
import org.springframework.kafka.listener.DefaultErrorHandler;
import org.springframework.util.backoff.FixedBackOff;

import java.util.HashMap;
import java.util.Map;

/**
 * Centralizes Kafka topic creation and consumer error handling for the async
 * analysis flow. Only active when analysis.mode=kafka.
 *
 * Topics created on startup (if they don't already exist):
 *   analysis.requested     – 3 partitions: backend publishes here on upload
 *   analysis.completed     – 3 partitions: ML service publishes results here
 *   analysis.completed.DLT – 1 partition: dead-letter topic for poison messages
 */
@Configuration
@ConditionalOnProperty(name = "analysis.mode", havingValue = "kafka", matchIfMissing = true)
public class KafkaConfig {

    @Bean
    public KafkaAdmin kafkaAdmin(@Value("${spring.kafka.bootstrap-servers:localhost:9092}") String bootstrapServers) {
        Map<String, Object> configs = new HashMap<>();
        configs.put(AdminClientConfig.BOOTSTRAP_SERVERS_CONFIG, bootstrapServers);
        return new KafkaAdmin(configs);
    }

    /** WHY 3 partitions: allows parallel ML consumers in Phase 4/5 for scaling. */
    @Bean
    public NewTopic analysisRequestedTopic() {
        return TopicBuilder.name("analysis.requested")
                .partitions(3)
                .replicas(1)
                .build();
    }

    @Bean
    public NewTopic analysisCompletedTopic() {
        return TopicBuilder.name("analysis.completed")
                .partitions(3)
                .replicas(1)
                .build();
    }

    /** Dead-letter topic for messages that repeatedly fail consumer processing. */
    @Bean
    public NewTopic analysisCompletedDltTopic() {
        return TopicBuilder.name("analysis.completed.DLT")
                .partitions(1)
                .replicas(1)
                .build();
    }

    @Bean
    public DefaultErrorHandler kafkaErrorHandler(KafkaTemplate<Object, Object> kafkaTemplate) {
        DeadLetterPublishingRecoverer recoverer =
                new DeadLetterPublishingRecoverer(kafkaTemplate);
        return new DefaultErrorHandler(recoverer, new FixedBackOff(1000L, 3));
    }
}
