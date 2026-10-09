import { expect, test } from "@playwright/test";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import ts from "typescript";

function files(directory: string): string[] {
  return readdirSync(directory, { withFileTypes: true }).flatMap(entry => entry.isDirectory()
    ? files(join(directory, entry.name))
    : /\.tsx?$/.test(entry.name) ? [join(directory, entry.name)] : []);
}
function sources(node: ts.Node | undefined): string[] {
  if (!node) return [];
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return [node.text];
  if (ts.isConditionalExpression(node)) return [...sources(node.whenTrue), ...sources(node.whenFalse)];
  if (ts.isParenthesizedExpression(node)) return sources(node.expression);
  if (ts.isBinaryExpression(node) && [ts.SyntaxKind.QuestionQuestionToken, ts.SyntaxKind.BarBarToken].includes(node.operatorToken.kind)) return [...sources(node.left), ...sources(node.right)];
  return [];
}

test("portal copy has Chinese entries and valid interpolation placeholders", () => {
  const catalog: Record<string, string> = Object.assign({}, ...["zh-SG", "insurance.zh-SG", "diagnoses.zh-SG"].map(name => JSON.parse(readFileSync(`src/i18n/${name}.json`, "utf8"))));
  const known = new Set(Object.keys(catalog).map(key => key.trim().toLowerCase()));
  const missing: string[] = [];
  for (const file of files("src")) {
    const ast = ts.createSourceFile(file, readFileSync(file, "utf8"), ts.ScriptTarget.Latest, true);
    function visit(node: ts.Node) {
      if (ts.isCallExpression(node) && /^(pt|portalText|portalMessage)$/.test(node.expression.getText(ast))) {
        for (const source of sources(node.arguments[0])) if (/[a-z]{2}/i.test(source) && !known.has(source.trim().toLowerCase())) missing.push(`${file}: ${source}`);
      }
      ts.forEachChild(node, visit);
    }
    visit(ast);
  }
  expect(missing).toEqual([]);
  for (const [source, translated] of Object.entries(catalog)) {
    // Chinese deliberately omits the English plural suffix.
    if (source !== "s") expect(translated.trim(), source).not.toBe("");
    const original = new Set([...source.matchAll(/\{(\d+)\}/g)].map(match => match[1]));
    const invented = [...translated.matchAll(/\{(\d+)\}/g)].map(match => match[1]).filter(index => !original.has(index));
    expect(invented, source).toEqual([]);
  }
});
