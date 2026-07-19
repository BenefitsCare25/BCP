/** Panel clinics — two sibling surfaces under one settings page.
 *
 * "Locations" is a SHARED library of clinic network lists: each row is one
 * network (insurer + panel provider + country + clinic type) uploaded once and
 * available to every company. The "Enabled" switch is the per-company
 * selection: it tags the listing to the ACTIVE company's policy year, which is
 * what exposes its clinics to that company's members via /portal/clinics.
 *
 * "Cards" is the matching e-card library (artwork + printed-field layout),
 * assigned per benefit year and product — see cards/PanelCardsPanel.
 *
 * Both are operational (not locked by activation) — panel networks and card
 * artwork change mid-year.
 */
import { useMemo, useRef, useState } from "react";
import {
  Building2,
  Download,
  Loader2,
  MapPin,
  Pencil,
  Plus,
  Trash2,
  Upload,
} from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import {
  CLINIC_TYPE_OPTIONS,
  COUNTRY_OPTIONS,
  useCreatePanelListing,
  useDeletePanelListing,
  useListingCompanies,
  usePanelListings,
  usePolicyYearPanels,
  useSetListingCompanies,
  useSetPolicyYearPanels,
  useUpdatePanelListing,
  useUploadPanelList,
  type PanelListing,
  type PanelListingInput,
} from "@/api/panelListings";
import { usePolicyYears } from "@/api/hooks";
import { PanelCardsPanel } from "@/components/configuration/cards/PanelCardsPanel";
import { PanelSetupHistory } from "@/components/configuration/PanelSetupHistory";
import { AlertDialog } from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Card,
  CardContent,
  CardHeader,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Sheet,
  SheetBody,
  SheetContent,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import { Switch } from "@/components/ui/switch";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { InfoHint } from "@/components/ui/tooltip";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatError } from "@/lib/errors";
import { downloadResponseAsFile } from "@/lib/download";
import { useSession } from "@/stores/session";

type TabKey = "locations" | "cards" | "history";

const EMPTY_FORM: PanelListingInput = {
  insurer: "",
  panel_provider: "",
  country: "SG",
  clinic_type: "gp",
  label: "",
};

/** Mounted only while open (the parent keys it by the edited listing), so
 * the initial state is always freshly seeded — no render-phase reseeding. */
function ListingFormSheet({
  onOpenChange,
  editing,
}: {
  onOpenChange: (open: boolean) => void;
  editing: PanelListing | null;
}) {
  const create = useCreatePanelListing();
  const update = useUpdatePanelListing();
  const [form, setForm] = useState<PanelListingInput>(() =>
    editing
      ? {
          insurer: editing.insurer,
          panel_provider: editing.panel_provider,
          country: editing.country,
          clinic_type: editing.clinic_type,
          label: editing.label ?? "",
        }
      : EMPTY_FORM,
  );
  const pending = create.isPending || update.isPending;
  const valid = form.insurer.trim() !== "" && form.panel_provider.trim() !== "";

  const submit = async () => {
    const payload = {
      ...form,
      insurer: form.insurer.trim(),
      panel_provider: form.panel_provider.trim(),
      label: form.label?.trim() || null,
    };
    try {
      if (editing) {
        await update.mutateAsync({ id: editing.id, ...payload });
        toast.success("Panel listing updated");
      } else {
        await create.mutateAsync(payload);
        toast.success(
          "Panel listing created — upload its clinic list to make it usable",
        );
      }
      onOpenChange(false);
    } catch {
      // Global mutation toast already surfaced the error.
    }
  };

  return (
    <Sheet open onOpenChange={onOpenChange}>
      <SheetContent>
        <SheetHeader>
          <SheetTitle>
            {editing ? "Edit panel listing" : "New panel listing"}
          </SheetTitle>
        </SheetHeader>
        <SheetBody className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="panel-insurer">Insurer</Label>
            <Input
              id="panel-insurer"
              value={form.insurer}
              placeholder="e.g. AIA-SG"
              onChange={(e) => setForm({ ...form, insurer: e.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="panel-provider">Panel provider</Label>
            <Input
              id="panel-provider"
              value={form.panel_provider}
              placeholder="e.g. Alliance, Fullerton, MHC"
              onChange={(e) =>
                setForm({ ...form, panel_provider: e.target.value })
              }
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>Country</Label>
              <Select
                value={form.country}
                onValueChange={(v) =>
                  setForm({ ...form, country: v as PanelListingInput["country"] })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {COUNTRY_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Clinic type</Label>
              <Select
                value={form.clinic_type}
                onValueChange={(v) =>
                  setForm({
                    ...form,
                    clinic_type: v as PanelListingInput["clinic_type"],
                  })
                }
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CLINIC_TYPE_OPTIONS.map((o) => (
                    <SelectItem key={o.value} value={o.value}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="panel-label">Display label (optional)</Label>
            <Input
              id="panel-label"
              value={form.label ?? ""}
              placeholder="Defaults to insurer + provider + type"
              onChange={(e) => setForm({ ...form, label: e.target.value })}
            />
          </div>
        </SheetBody>
        <SheetFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={() => void submit()} disabled={!valid || pending}>
            {pending && <Loader2 className="size-4 animate-spin" />}
            {editing ? "Save changes" : "Create listing"}
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

/** "Enable for companies" — tick every company whose plan uses this network;
 * each checkbox targets that company's current policy year. */
function CompaniesSheet({
  listing,
  onOpenChange,
}: {
  listing: PanelListing;
  onOpenChange: (open: boolean) => void;
}) {
  const { data: companies, isLoading } = useListingCompanies(listing.id);
  const save = useSetListingCompanies();
  // null until first interaction — the server state renders untouched.
  const [selected, setSelected] = useState<Set<string> | null>(null);
  const effective =
    selected ??
    new Set((companies ?? []).filter((c) => c.enabled).map((c) => c.client_id));

  const toggle = (clientId: string, next: boolean) => {
    const ids = new Set(effective);
    if (next) ids.add(clientId);
    else ids.delete(clientId);
    setSelected(ids);
  };

  const submit = async () => {
    try {
      await save.mutateAsync({
        listingId: listing.id,
        clientIds: [...effective],
      });
      toast.success(
        `${listing.display_label} is now enabled for ${effective.size} ${effective.size === 1 ? "company" : "companies"}`,
      );
      onOpenChange(false);
    } catch {
      // Global mutation toast already surfaced the error.
    }
  };

  return (
    <Sheet open onOpenChange={onOpenChange}>
      <SheetContent>
        <SheetHeader>
          <SheetTitle className="flex items-center gap-1.5">
            Enable for companies
            <InfoHint>
              Companies ticked here will show {listing.display_label} to their
              members. Each selection applies to that company's current policy
              year and carries over automatically when a new year is created.
            </InfoHint>
          </SheetTitle>
        </SheetHeader>
        <SheetBody className="space-y-3">
          {isLoading ? (
            <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> Loading companies…
            </div>
          ) : (
            <div className="space-y-1">
              {(companies ?? []).map((company) => {
                const noYear = company.policy_year_id === null;
                return (
                  <label
                    key={company.client_id}
                    className={
                      "flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2 " +
                      (noYear ? "opacity-60" : "cursor-pointer hover:bg-muted/60")
                    }
                  >
                    <span className="flex items-center gap-2.5">
                      <Checkbox
                        checked={effective.has(company.client_id)}
                        disabled={noYear || save.isPending}
                        onCheckedChange={(v) =>
                          toggle(company.client_id, v === true)
                        }
                      />
                      <span className="text-sm text-foreground">
                        {company.client_name}
                      </span>
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {noYear ? (
                        <span className="text-warn">No policy year yet</span>
                      ) : (
                        company.policy_year_label
                      )}
                    </span>
                  </label>
                );
              })}
            </div>
          )}
        </SheetBody>
        <SheetFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={() => void submit()}
            disabled={isLoading || save.isPending || selected === null}
          >
            {save.isPending && <Loader2 className="size-4 animate-spin" />}
            Save
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

function ListingRow({
  listing,
  tagged,
  tagPending,
  onToggleTag,
  onCompanies,
  onEdit,
  onDelete,
}: {
  listing: PanelListing;
  tagged: boolean;
  tagPending: boolean;
  onToggleTag: (next: boolean) => void;
  onCompanies: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const upload = useUploadPanelList();
  const [downloading, setDownloading] = useState(false);

  const handleFile = async (file: File | undefined) => {
    if (!file) return;
    try {
      const result = await upload.mutateAsync({ id: listing.id, file });
      const notes = [
        result.skipped_no_name > 0 &&
          `${result.skipped_no_name} rows without a name skipped`,
        result.missing_coordinates > 0 &&
          `${result.missing_coordinates} without map coordinates`,
      ]
        .filter(Boolean)
        .join("; ");
      toast.success(
        `${result.imported} clinics imported into ${listing.display_label}` +
          (notes ? ` (${notes})` : ""),
      );
    } catch (error) {
      toast.error(formatError(error));
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const handleDownload = async () => {
    setDownloading(true);
    try {
      // The server names the file via Content-Disposition; the arg is a fallback.
      await downloadResponseAsFile(
        await api.downloadResponse(`/panel-listings/${listing.id}/download`),
        "panel_clinics.xlsx",
      );
    } catch (error) {
      toast.error(formatError(error));
    } finally {
      setDownloading(false);
    }
  };

  return (
    <TableRow>
      <TableCell className="font-medium text-foreground">
        {listing.insurer}
      </TableCell>
      <TableCell>{listing.panel_provider}</TableCell>
      <TableCell>{listing.country}</TableCell>
      <TableCell>
        <Badge variant="outline">{listing.type_label}</Badge>
      </TableCell>
      <TableCell className="tabular-nums">
        {listing.clinic_count > 0 ? (
          listing.clinic_count
        ) : (
          <span className="text-warn">No list uploaded</span>
        )}
      </TableCell>
      <TableCell className="tabular-nums text-muted-foreground">
        {listing.tagged_policy_year_ids.length}
      </TableCell>
      <TableCell>
        <Switch
          checked={tagged}
          disabled={tagPending || listing.clinic_count === 0}
          onCheckedChange={onToggleTag}
          aria-label={`Enable ${listing.display_label} for the selected company's policy year`}
        />
      </TableCell>
      <TableCell>
        <div className="flex items-center justify-end gap-1">
          <input
            ref={fileRef}
            type="file"
            accept=".xlsx,.xlsm,.xls"
            className="hidden"
            onChange={(e) => void handleFile(e.target.files?.[0])}
          />
          <Button
            variant="ghost"
            size="sm"
            disabled={listing.clinic_count === 0}
            onClick={onCompanies}
            title="Enable for companies — tick every company that uses this network"
          >
            <Building2 className="size-4" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={upload.isPending}
            onClick={() => fileRef.current?.click()}
            title="Upload clinic list (replaces the current list for ALL companies)"
          >
            {upload.isPending ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Upload className="size-4" />
            )}
          </Button>
          <Button
            variant="ghost"
            size="sm"
            disabled={downloading || listing.clinic_count === 0}
            onClick={() => void handleDownload()}
            title="Download the current clinic list"
          >
            {downloading ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <Download className="size-4" />
            )}
          </Button>
          <Button variant="ghost" size="sm" onClick={onEdit} title="Edit listing">
            <Pencil className="size-4" />
          </Button>
          <Button
            variant="ghost"
            size="sm"
            onClick={onDelete}
            title="Delete listing"
          >
            <Trash2 className="size-4 text-error" />
          </Button>
        </div>
      </TableCell>
    </TableRow>
  );
}

function LibraryTable({
  listings,
  yearLabel,
  taggedIds,
  tagPending,
  onToggleTag,
  onCompanies,
  onEdit,
  onDelete,
}: {
  listings: PanelListing[];
  yearLabel: string;
  taggedIds: Set<string>;
  tagPending: boolean;
  onToggleTag: (listing: PanelListing, next: boolean) => void;
  onCompanies: (listing: PanelListing) => void;
  onEdit: (listing: PanelListing) => void;
  onDelete: (listing: PanelListing) => void;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Insurer</TableHead>
          <TableHead>Panel provider</TableHead>
          <TableHead>Country</TableHead>
          <TableHead>Clinic type</TableHead>
          <TableHead>Clinics</TableHead>
          <TableHead title="Policy years (across all companies) this listing is enabled for">
            Used by
          </TableHead>
          <TableHead>Enabled{yearLabel ? ` (${yearLabel})` : ""}</TableHead>
          <TableHead className="text-right">Actions</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {listings.map((listing) => (
          <ListingRow
            key={listing.id}
            listing={listing}
            tagged={taggedIds.has(listing.id)}
            tagPending={tagPending}
            onToggleTag={(next) => onToggleTag(listing, next)}
            onCompanies={() => onCompanies(listing)}
            onEdit={() => onEdit(listing)}
            onDelete={() => onDelete(listing)}
          />
        ))}
      </TableBody>
    </Table>
  );
}

export function PanelClinicsPage() {
  const policyYearId = useSession((s) => s.currentPolicyYearId);
  const [tab, setTab] = useState<TabKey>("locations");
  const { data: policyYears = [] } = usePolicyYears();
  const { data: listings = [], isLoading } = usePanelListings();
  // Only query with a year id CONFIRMED in the active client's list — the
  // persisted selection can point at another company's (or a deleted) year
  // for a moment after switching, which would 404 as "Policy year not found".
  const currentYear = policyYears.find((y) => y.id === policyYearId);
  const validYearId = currentYear?.id;
  const { data: yearPanels } = usePolicyYearPanels(validYearId);
  const setPanels = useSetPolicyYearPanels();
  const remove = useDeletePanelListing();

  const [formOpen, setFormOpen] = useState(false);
  const [editing, setEditing] = useState<PanelListing | null>(null);
  const [deleting, setDeleting] = useState<PanelListing | null>(null);
  const [companiesFor, setCompaniesFor] = useState<PanelListing | null>(null);
  const taggedIds = useMemo(
    () => new Set(yearPanels?.panel_listing_ids ?? []),
    [yearPanels],
  );

  const toggleTag = (listing: PanelListing, next: boolean) => {
    if (!validYearId) return;
    const ids = new Set(taggedIds);
    if (next) ids.add(listing.id);
    else ids.delete(listing.id);
    setPanels.mutate(
      { policyYearId: validYearId, panelListingIds: [...ids] },
      {
        onSuccess: () =>
          toast.success(
            next
              ? `${listing.display_label} enabled for policy year ${currentYear?.year ?? ""} — members can now find these clinics`
              : `${listing.display_label} disabled for policy year ${currentYear?.year ?? ""}`,
          ),
      },
    );
  };

  const confirmDelete = async () => {
    if (!deleting) return;
    try {
      await remove.mutateAsync(deleting.id);
      toast.success(`${deleting.display_label} deleted`);
      setDeleting(null);
    } catch {
      // Global mutation toast already surfaced the error.
    }
  };

  return (
    <Tabs
      value={tab}
      onValueChange={(v) => setTab(v as TabKey)}
      className="space-y-4"
    >
      <TabsList>
        <TabsTrigger value="locations">Locations</TabsTrigger>
        <TabsTrigger value="cards">Cards</TabsTrigger>
        <TabsTrigger value="history">History</TabsTrigger>
      </TabsList>

      <TabsContent value="history">
        <PanelSetupHistory />
      </TabsContent>

      <TabsContent value="cards">
        <PanelCardsPanel
          policyYearId={validYearId}
          yearLabel={currentYear ? String(currentYear.year) : ""}
        />
      </TabsContent>

      <TabsContent value="locations" className="space-y-4">
      <Card>
        <CardHeader className="flex-row items-start justify-end space-y-0">
          <Button
            size="sm"
            onClick={() => {
              setEditing(null);
              setFormOpen(true);
            }}
          >
            <Plus className="size-4" />
            <span className="ml-1">New listing</span>
          </Button>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" /> Loading panel
              listings…
            </div>
          ) : listings.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border p-8 text-center">
              <MapPin className="mx-auto size-6 text-muted-foreground" />
              <p className="mt-2 text-sm font-medium text-foreground">
                The panel library is empty
              </p>
              <p className="mt-1 text-xs text-muted-foreground">
                Create a listing per insurer, panel provider and clinic type
                (e.g. AIA-SG · Alliance · SG GP), upload its clinic workbook,
                then enable it for each company that uses that network.
              </p>
            </div>
          ) : (
            <LibraryTable
              listings={listings}
              yearLabel={currentYear ? String(currentYear.year) : ""}
              taggedIds={taggedIds}
              tagPending={setPanels.isPending || !validYearId}
              onToggleTag={toggleTag}
              onCompanies={setCompaniesFor}
              onEdit={(listing) => {
                setEditing(listing);
                setFormOpen(true);
              }}
              onDelete={setDeleting}
            />
          )}
        </CardContent>
      </Card>

      {formOpen && (
        <ListingFormSheet
          key={editing?.id ?? "new"}
          editing={editing}
          onOpenChange={setFormOpen}
        />
      )}

      {companiesFor && (
        <CompaniesSheet
          key={companiesFor.id}
          listing={companiesFor}
          onOpenChange={(open) => !open && setCompaniesFor(null)}
        />
      )}

      <AlertDialog
        open={deleting !== null}
        onOpenChange={(open) => !open && setDeleting(null)}
        title="Delete panel listing?"
        description={
          deleting
            ? `${deleting.display_label} and its ${deleting.clinic_count} clinics will be removed from the shared library and from every company/policy year it is enabled for (${deleting.tagged_policy_year_ids.length}). Members will no longer see these clinics in the locator.`
            : ""
        }
        loading={remove.isPending}
        onConfirm={() => void confirmDelete()}
      />
      </TabsContent>
    </Tabs>
  );
}
