/** Chữ cái đầu để làm avatar chữ: "An Hoang" -> "AH", "Acme" -> "AC". */
export function initials(name: string): string {
  const words = name.split(/\s+/).filter(Boolean);
  const letters = words.length >= 2 ? words[0][0] + words[1][0] : name.slice(0, 2);
  return letters.toUpperCase();
}
