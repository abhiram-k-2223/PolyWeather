import type { DocsNavGroup } from "./docs";

export const DOCS_GROUPS: DocsNavGroup[] = [
  {
    id: "getting-started",
    title: "Getting Started",
  },
  {
    id: "settlement",
    title: "Settlement & Data",
  },
];

export function getDocsGroupTitle(groupId: DocsNavGroup["id"]) {
  return DOCS_GROUPS.find((group) => group.id === groupId)?.title || groupId;
}