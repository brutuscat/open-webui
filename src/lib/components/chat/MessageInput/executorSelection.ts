// Native Harmony exposes Jupyter and the terminal as independent executors.
// Ordinary and mixed model selections retain terminal/interpreter exclusivity.
export const allowsConcurrentCodeInterpreter = (
	modelIds: string[],
	capabilitiesById: Record<string, { gpt_oss_harmony_native_tools?: boolean }>,
	interpreterEngine: string | undefined
): boolean =>
	interpreterEngine === 'jupyter' &&
	modelIds.length > 0 &&
	modelIds.every((id) => capabilitiesById[id]?.gpt_oss_harmony_native_tools === true);
