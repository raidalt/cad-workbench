/**
 * Clear-board intent detection. The point is only to stop the chat from
 * guessing: a message that asks to clear/wipe/empty the board gets an explicit
 * choice card instead of a model mutation.
 */

const ACTION = /\b(clear|wipe|empty|erase|blank)\b/i;
const TARGET =
  /\b(board|model|viewport|scene|parts?|everything|all|it|this|that)\b/i;
const PHRASE =
  /\b(delete (everything|all)|start over|start from scratch|blank slate|remove (everything|all|every part|each part))\b/i;

export function wantsClearBoard(text: string): boolean {
  const value = text.trim();
  if (!value) return false;
  if (PHRASE.test(value)) return true;
  if (!ACTION.test(value)) return false;
  // "wipe it" / "clear the board" / "empty the model" / "blank"
  return TARGET.test(value) || value.split(/\s+/).length <= 4;
}

/**
 * A completed reply that proposes clearing the model. Narrow on purpose: it
 * matches first-person proposals, not incidental uses of the words.
 */
export function replyProposesClear(text: string): boolean {
  const value = text.trim();
  if (!value) return false;
  if (PHRASE.test(value)) return true;
  return (
    /\b(i('| wi)ll|i can|i could|let me|we can|to)\b[^.\n]{0,48}\b(clear|wipe|empty|erase|blank|remove (all|every|each))\b/i.test(
      value,
    ) || /\b(clear|wipe|empty)\s+(the\s+)?(board|model|viewport|scene|parts?)\b/i.test(value)
  );
}
