package com.odh.auth.config;

import com.odh.auth.jwt.JwtProperties;
import com.odh.auth.metrics.MetricsProperties;
import com.odh.auth.ops.OpsProperties;
import com.odh.common.servlet.ServiceFilterConfiguration;
import com.odh.common.web.CommonExceptionHandler;
import org.springframework.boot.context.properties.EnableConfigurationProperties;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Import;

@Configuration
@EnableConfigurationProperties({JwtProperties.class, OpsProperties.class, MetricsProperties.class})
@Import({ServiceFilterConfiguration.class, CommonExceptionHandler.class})
public class AuthConfiguration {
}
