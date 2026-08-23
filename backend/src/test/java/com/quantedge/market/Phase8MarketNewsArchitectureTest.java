package com.quantedge.market;

import com.quantedge.economic.controller.EconomicCalendarController;
import com.quantedge.market.controller.MarketDataController;
import com.quantedge.news.controller.NewsController;
import com.quantedge.notification.controller.NotificationController;
import com.quantedge.trading.service.OrderExecutionService;
import org.junit.jupiter.api.DisplayName;
import org.junit.jupiter.api.Test;

import java.lang.reflect.Method;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.List;
import java.util.stream.Stream;

import static org.assertj.core.api.Assertions.assertThat;

@DisplayName("Phase 8: Architecture Invariant & Boundary Tests")
class Phase8MarketNewsArchitectureTest {

    @Test
    @DisplayName("INVARIANT: Market, News, and Economic modules have zero execution dependencies")
    void verifyNoExecutionDependencies() {
        Class<?>[] phase8Controllers = new Class<?>[]{
                MarketDataController.class,
                NewsController.class,
                EconomicCalendarController.class,
                NotificationController.class
        };

        for (Class<?> controller : phase8Controllers) {
            for (Method method : controller.getDeclaredMethods()) {
                assertThat(method.getName().toLowerCase())
                        .as("Phase 8 controllers must not have order execution methods: %s.%s", controller.getSimpleName(), method.getName())
                        .doesNotContain("placeorder")
                        .doesNotContain("executeorder")
                        .doesNotContain("submittrade");
            }
        }
    }

    @Test
    @DisplayName("INVARIANT: Sole real-order authority remains OrderExecutionService")
    void verifySoleRealOrderAuthority() throws Exception {
        Path backendSrc = Paths.get("src/main/java");
        if (!Files.exists(backendSrc)) {
            backendSrc = Paths.get("backend/src/main/java");
        }

        try (Stream<Path> stream = Files.walk(backendSrc)) {
            List<Path> filesWithPostOrders = stream
                    .filter(p -> p.toString().endsWith(".java"))
                    .filter(p -> {
                        try {
                            String content = Files.readString(p);
                            return content.contains("/v2/orders") && content.contains("POST");
                        } catch (Exception e) {
                            return false;
                        }
                    })
                    .toList();

            assertThat(filesWithPostOrders).hasSize(1);
            assertThat(filesWithPostOrders.getFirst().toString())
                    .contains("OrderExecutionService");
        }
    }
}
