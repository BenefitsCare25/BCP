// Re-export shim (stable import surface). The implementation moved to
// ./sob/ when the editor was rebuilt from a card stack into a single table:
// SobEditor (shell + filter + column manager) → SobRow → SobRowDetail → SobCell.
export { SobEditor as ScheduleOfBenefitsSection } from "./sob/SobEditor";
