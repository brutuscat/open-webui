import { describe, expect, it } from 'vitest';
import { allowsConcurrentCodeInterpreter } from './executorSelection';

describe('terminal and Code Interpreter selection', () => {
	const capabilities = {
		harmony: { gpt_oss_harmony_native_tools: true },
		otherHarmony: { gpt_oss_harmony_native_tools: true },
		ordinary: {},
		disabled: { gpt_oss_harmony_native_tools: false }
	};

	it('allows both executors for explicitly enabled Harmony models', () => {
		expect(allowsConcurrentCodeInterpreter(['harmony'], capabilities, 'jupyter')).toBe(true);
		expect(allowsConcurrentCodeInterpreter(['harmony', 'otherHarmony'], capabilities, 'jupyter')).toBe(true);
	});

	it('preserves exclusivity for ordinary, mixed and disabled models', () => {
		for (const ids of [['ordinary'], ['harmony', 'ordinary'], ['disabled']]) {
			expect(allowsConcurrentCodeInterpreter(ids, capabilities, 'jupyter')).toBe(false);
		}
	});

	it('does not infer eligibility from an empty or unknown selection', () => {
		expect(allowsConcurrentCodeInterpreter([], capabilities, 'jupyter')).toBe(false);
		expect(allowsConcurrentCodeInterpreter(['missing'], capabilities, 'jupyter')).toBe(false);
	});

	it('preserves exclusivity for browser Python and an unconfigured engine', () => {
		expect(allowsConcurrentCodeInterpreter(['harmony'], capabilities, 'pyodide')).toBe(false);
		expect(allowsConcurrentCodeInterpreter(['harmony'], capabilities, undefined)).toBe(false);
	});
});
