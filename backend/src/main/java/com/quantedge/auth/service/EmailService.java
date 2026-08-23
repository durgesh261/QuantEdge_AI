package com.quantedge.auth.service;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.mail.javamail.JavaMailSender;
import org.springframework.mail.javamail.MimeMessageHelper;
import org.springframework.stereotype.Service;

import jakarta.mail.MessagingException;
import jakarta.mail.internet.MimeMessage;

/**
 * Email delivery service.
 *
 * If Spring Mail is not configured (MAIL_HOST absent), the service runs in
 * DEV_LOG mode: it logs the reset URL to the application log instead of
 * sending a real email. Dev log mode is explicitly gated on the
 * MAIL_DEV_LOG_ENABLED=true environment variable — it is NEVER active in
 * production (where MAIL_HOST is required).
 *
 * Reset tokens are NEVER exposed in normal production logs.
 */
@Service
public class EmailService {

    private static final Logger log = LoggerFactory.getLogger(EmailService.class);

    private final JavaMailSender mailSender;

    @Value("${spring.mail.from:noreply@quantedge.ai}")
    private String fromAddress;

    @Value("${quantedge.mail.dev-log-enabled:false}")
    private boolean devLogEnabled;

    @Value("${spring.mail.host:}")
    private String mailHost;

    public EmailService(JavaMailSender mailSender) {
        this.mailSender = mailSender;
    }

    /**
     * Sends a password-reset email.
     *
     * @param toEmail      recipient email
     * @param displayName  recipient display name
     * @param resetUrl     full reset URL containing the one-time token
     */
    public void sendPasswordResetEmail(String toEmail, String displayName, String resetUrl) {
        boolean smtpConfigured = mailHost != null && !mailHost.isBlank();

        if (!smtpConfigured) {
            if (devLogEnabled) {
                // DEV ONLY: log reset link so developers can test without SMTP
                log.info("[DEV] Password reset link for {} → {}", toEmail, resetUrl);
            } else {
                log.warn("[EmailService] SMTP not configured. Password reset email NOT sent to {}", toEmail);
            }
            return;
        }

        try {
            MimeMessage message = mailSender.createMimeMessage();
            MimeMessageHelper helper = new MimeMessageHelper(message, true, "UTF-8");

            helper.setFrom(fromAddress);
            helper.setTo(toEmail);
            helper.setSubject("QuantEdge AI — Password Reset Request");
            helper.setText(buildHtmlBody(displayName, resetUrl), true);

            mailSender.send(message);
            log.info("[EmailService] Password reset email sent to {}", toEmail);
        } catch (MessagingException e) {
            log.error("[EmailService] Failed to send password reset email to {}: {}", toEmail, e.getMessage());
            // Do not rethrow — caller already returned generic response to prevent enumeration
        }
    }

    private String buildHtmlBody(String name, String resetUrl) {
        return """
                <!DOCTYPE html>
                <html lang="en">
                <head>
                  <meta charset="UTF-8">
                  <meta name="viewport" content="width=device-width, initial-scale=1.0">
                  <title>QuantEdge AI — Password Reset</title>
                </head>
                <body style="margin:0;padding:0;background:#0a0e1a;font-family:'Segoe UI',Arial,sans-serif;">
                  <table width="100%%" cellpadding="0" cellspacing="0" style="background:#0a0e1a;padding:40px 0;">
                    <tr>
                      <td align="center">
                        <table width="520" cellpadding="0" cellspacing="0" style="background:#111827;border:1px solid #1e2a3a;border-radius:12px;overflow:hidden;">
                          <!-- Header -->
                          <tr>
                            <td style="background:linear-gradient(135deg,#0ea5e9,#3b82f6);padding:28px 32px;text-align:center;">
                              <p style="margin:0;font-size:20px;font-weight:700;color:#ffffff;letter-spacing:1px;font-family:monospace;">
                                QUANTEDGE AI
                              </p>
                              <p style="margin:6px 0 0;font-size:11px;color:rgba(255,255,255,0.75);letter-spacing:0.5px;">
                                Institutional Algorithmic Trading Platform
                              </p>
                            </td>
                          </tr>
                          <!-- Body -->
                          <tr>
                            <td style="padding:32px;">
                              <p style="font-size:16px;color:#e2e8f0;margin:0 0 12px;">Hi %s,</p>
                              <p style="font-size:14px;color:#94a3b8;margin:0 0 24px;line-height:1.7;">
                                We received a request to reset the password for your QuantEdge AI account.
                                Click the button below to set a new password.
                              </p>
                              <div style="text-align:center;margin:28px 0;">
                                <a href="%s"
                                   style="display:inline-block;padding:14px 32px;background:#0ea5e9;color:#fff;text-decoration:none;border-radius:8px;font-weight:600;font-size:14px;letter-spacing:0.3px;">
                                  Reset My Password
                                </a>
                              </div>
                              <p style="font-size:12px;color:#64748b;margin:0 0 8px;">
                                Or paste this link into your browser:
                              </p>
                              <p style="font-size:11px;color:#0ea5e9;word-break:break-all;margin:0 0 24px;">%s</p>
                              <hr style="border:none;border-top:1px solid #1e2a3a;margin:24px 0;">
                              <p style="font-size:12px;color:#64748b;margin:0 0 8px;line-height:1.6;">
                                ⏱ This link expires in <strong style="color:#e2e8f0;">30 minutes</strong>.
                              </p>
                              <p style="font-size:12px;color:#64748b;margin:0;line-height:1.6;">
                                🔒 If you did not request this, please ignore this email — your account remains secure.
                                Never share this link with anyone.
                              </p>
                            </td>
                          </tr>
                          <!-- Footer -->
                          <tr>
                            <td style="background:#0d1424;padding:16px 32px;text-align:center;">
                              <p style="font-size:11px;color:#374151;margin:0;">
                                QuantEdge AI · Institutional Trading Platform · This is an automated email, please do not reply.
                              </p>
                            </td>
                          </tr>
                        </table>
                      </td>
                    </tr>
                  </table>
                </body>
                </html>
                """.formatted(name, resetUrl, resetUrl);
    }
}
