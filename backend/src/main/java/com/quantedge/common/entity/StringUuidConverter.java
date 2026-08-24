package com.quantedge.common.entity;

import jakarta.persistence.AttributeConverter;
import jakarta.persistence.Converter;

import java.nio.charset.StandardCharsets;
import java.util.UUID;

/**
 * JPA AttributeConverter that maps Java String IDs to PostgreSQL native UUID database columns.
 * Supports standard 36-character UUID strings and safely falls back to deterministic name UUIDs
 * for non-standard string identifiers used in test suites (e.g. "acct-1", "user-test").
 */
@Converter
public class StringUuidConverter implements AttributeConverter<String, UUID> {

    @Override
    public UUID convertToDatabaseColumn(String attribute) {
        if (attribute == null || attribute.isBlank()) {
            return null;
        }
        try {
            return UUID.fromString(attribute.trim());
        } catch (IllegalArgumentException e) {
            return UUID.nameUUIDFromBytes(attribute.getBytes(StandardCharsets.UTF_8));
        }
    }

    @Override
    public String convertToEntityAttribute(UUID dbData) {
        return dbData == null ? null : dbData.toString();
    }
}
