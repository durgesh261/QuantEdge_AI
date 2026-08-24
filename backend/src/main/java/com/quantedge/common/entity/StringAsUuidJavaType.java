package com.quantedge.common.entity;

import org.hibernate.type.descriptor.WrapperOptions;
import org.hibernate.type.descriptor.java.AbstractClassJavaType;
import org.hibernate.type.descriptor.jdbc.JdbcType;
import org.hibernate.type.descriptor.jdbc.JdbcTypeIndicators;
import org.hibernate.type.descriptor.jdbc.UUIDJdbcType;

import java.nio.charset.StandardCharsets;
import java.util.UUID;

/**
 * Custom Hibernate 6 JavaType descriptor that maps Java String entity fields
 * to native PostgreSQL UUID columns with full schema validation and type unwrapping.
 */
public class StringAsUuidJavaType extends AbstractClassJavaType<String> {

    public static final StringAsUuidJavaType INSTANCE = new StringAsUuidJavaType();

    public StringAsUuidJavaType() {
        super(String.class);
    }

    @Override
    public JdbcType getRecommendedJdbcType(JdbcTypeIndicators context) {
        return UUIDJdbcType.INSTANCE;
    }

    @Override
    public String toString(String value) {
        return value;
    }

    @Override
    public String fromString(CharSequence string) {
        return string != null ? string.toString() : null;
    }

    @SuppressWarnings("unchecked")
    @Override
    public <X> X unwrap(String value, Class<X> type, WrapperOptions options) {
        if (value == null || value.isBlank()) {
            return null;
        }
        if (String.class.isAssignableFrom(type)) {
            return (X) value;
        }
        if (UUID.class.isAssignableFrom(type)) {
            try {
                return (X) UUID.fromString(value.trim());
            } catch (IllegalArgumentException e) {
                return (X) UUID.nameUUIDFromBytes(value.getBytes(StandardCharsets.UTF_8));
            }
        }
        throw unknownUnwrap(type);
    }

    @Override
    public <X> String wrap(X value, WrapperOptions options) {
        if (value == null) {
            return null;
        }
        if (value instanceof String s) {
            return s;
        }
        if (value instanceof UUID uuid) {
            return uuid.toString();
        }
        return value.toString();
    }
}
