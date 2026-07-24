package dev.aiteam.fixture;

import static org.junit.jupiter.api.Assertions.assertEquals;

import org.junit.jupiter.api.Test;

class CalculatorTest {
    @Test
    void addsTwoValues() {
        assertEquals(5, Calculator.add(2, 3));
    }
}
