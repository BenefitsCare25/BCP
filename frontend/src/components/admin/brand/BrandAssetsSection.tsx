import { useRef, useState } from "react";
import { ImageUp, Trash2 } from "lucide-react";
import { toast } from "sonner";
import type { PublicBrand } from "@/api/public";
import {
  BRAND_ASSET_SLOTS,
  type BrandAssetSlot,
  type BrandAssets,
  brandErrorMessage,
  useDeleteBrandAsset,
  useUploadBrandAsset,
} from "@/api/brand";
import { Button } from "@/components/ui/button";


const MAX_BYTES = 512 * 1024;
const RASTER = ["image/png", "image/webp"];
const ICO = ["image/x-icon", "image/vnd.microsoft.icon"];

interface SlotRule {
  label: string;
  help: string;
  types: string[];
  accept: string;
  /** null when the dimensions are acceptable. */
  dimensions: (width: number, height: number) => string | null;
}

const RULES: Record<BrandAssetSlot, SlotRule> = {
  logo: {
    label: "Logo",
    help: "PNG or WebP, at most 1200 × 400 pixels and 512 KB. Shown in headers and on sign-in pages.",
    types: RASTER,
    accept: ".png,.webp,image/png,image/webp",
    dimensions: (w, h) => (w <= 1200 && h <= 400 ? null : `This image is ${w} × ${h}; a logo can be at most 1200 × 400 pixels.`),
  },
  mark: {
    label: "Mark",
    help: "A square PNG or WebP, 64 to 1024 pixels, at most 512 KB. Used beside the name where there is no room for the logo.",
    types: RASTER,
    accept: ".png,.webp,image/png,image/webp",
    dimensions: (w, h) =>
      w === h && w >= 64 && w <= 1024 ? null : `This image is ${w} × ${h}; a mark must be square, 64 to 1024 pixels.`,
  },
  favicon: {
    label: "Browser icon",
    help: "A square PNG, WebP or ICO, 16 to 512 pixels, at most 512 KB. Shown in the browser tab.",
    types: [...RASTER, ...ICO],
    accept: ".png,.webp,.ico,image/png,image/webp,image/x-icon,image/vnd.microsoft.icon",
    dimensions: (w, h) =>
      w === h && w >= 16 && w <= 512 ? null : `This image is ${w} × ${h}; a browser icon must be square, 16 to 512 pixels.`,
  },
};

const PREVIEW: Record<BrandAssetSlot, keyof PublicBrand> = {
  logo: "logo_url",
  mark: "mark_url",
  favicon: "favicon_url",
};

function isIco(file: File): boolean {
  return ICO.includes(file.type) || file.name.toLowerCase().endsWith(".ico");
}

/** The same limits the server enforces, checked first so a wrong file is
 *  explained without an upload. The server still decides. */
async function precheck(slot: BrandAssetSlot, file: File): Promise<string | null> {
  const rule = RULES[slot];
  const ico = isIco(file);
  if (!rule.types.includes(file.type) && !(ico && slot === "favicon")) {
    return slot === "favicon" ? "Choose a PNG, WebP or ICO file." : "Choose a PNG or WebP file. SVG and JPEG are not accepted.";
  }
  if (file.size > MAX_BYTES) return `This file is ${Math.ceil(file.size / 1024)} KB; the limit is 512 KB.`;
  if (ico) return null; // Browsers can't reliably decode ICO; the server checks its size.
  try {
    const bitmap = await createImageBitmap(file);
    const problem = rule.dimensions(bitmap.width, bitmap.height);
    bitmap.close();
    return problem;
  } catch {
    return "This file couldn't be read as an image.";
  }
}

/** Logo, mark and browser icon for one scope ("firm" or a company id). Each
 *  upload or removal applies at once; it is not part of the form's Save. */
export function BrandAssetsSection({
  scope,
  assets,
  effective,
  inheritedFrom,
  readOnly,
}: {
  scope: string;
  assets: BrandAssets;
  /** The resolved brand for this scope, for previews. */
  effective: PublicBrand | null;
  /** Where a missing image comes from: "the platform default" or "the firm". */
  inheritedFrom: string;
  readOnly: boolean;
}) {
  return (
    <section aria-labelledby={`${scope}-brand-images`} className="space-y-3">
      <h4 id={`${scope}-brand-images`} className="text-sm font-medium text-foreground">
        Images
      </h4>
      <ul className="divide-y divide-border rounded-md border border-border">
        {BRAND_ASSET_SLOTS.map((slot) => (
          <AssetRow
            key={slot}
            scope={scope}
            slot={slot}
            own={assets[slot] ?? null}
            previewUrl={assets[slot]?.url ?? (effective?.[PREVIEW[slot]] as string | null | undefined) ?? null}
            inheritedFrom={inheritedFrom}
            readOnly={readOnly}
          />
        ))}
      </ul>
    </section>
  );
}

function AssetRow({
  scope,
  slot,
  own,
  previewUrl,
  inheritedFrom,
  readOnly,
}: {
  scope: string;
  slot: BrandAssetSlot;
  own: BrandAssets[BrandAssetSlot] | null;
  previewUrl: string | null;
  inheritedFrom: string;
  readOnly: boolean;
}) {
  const rule = RULES[slot];
  const input = useRef<HTMLInputElement>(null);
  const upload = useUploadBrandAsset(scope);
  const remove = useDeleteBrandAsset(scope);
  const [problem, setProblem] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const busy = checking || upload.isPending || remove.isPending;
  const id = `${scope}-asset-${slot}`;

  const choose = async (file: File | undefined) => {
    if (!file) return;
    setProblem(null);
    setChecking(true);
    const refusal = await precheck(slot, file);
    setChecking(false);
    if (refusal) {
      setProblem(refusal);
      return;
    }
    upload.mutate(
      { slot, file },
      {
        onSuccess: () => toast.success(`${rule.label} uploaded`),
        onError: (error) => setProblem(brandErrorMessage(error)),
      },
    );
  };

  return (
    <li className="flex flex-wrap items-start gap-4 px-3 py-3">
      <div className="flex h-14 w-32 shrink-0 items-center justify-center rounded-md border border-border bg-card p-1.5">
        {previewUrl ? (
          <img src={previewUrl} alt={`Current ${rule.label.toLowerCase()}`} className="max-h-full max-w-full object-contain" />
        ) : (
          <span className="text-2xs text-subtle">None</span>
        )}
      </div>
      <div className="min-w-0 flex-1 space-y-1">
        <p className="text-sm font-medium text-foreground">{rule.label}</p>
        <p id={`${id}-help`} className="text-xs text-muted-foreground">{rule.help}</p>
        <p className="text-xs text-subtle">
          {own
            ? `Your own: ${own.width} × ${own.height} px, ${Math.ceil(own.bytes / 1024)} KB.`
            : `Inherited from ${inheritedFrom}.`}
        </p>
        {problem && (
          <p role="alert" className="text-xs text-error">
            {problem}
          </p>
        )}
      </div>
      {!readOnly && (
        <div className="flex shrink-0 gap-1.5">
          <input
            ref={input}
            id={id}
            type="file"
            accept={rule.accept}
            className="sr-only"
            tabIndex={-1}
            aria-describedby={`${id}-help`}
            onChange={(event) => {
              void choose(event.target.files?.[0]);
              event.target.value = "";
            }}
          />
          <Button
            type="button"
            variant="outline"
            size="sm"
            loading={checking || upload.isPending}
            disabled={busy}
            onClick={() => input.current?.click()}
          >
            <ImageUp className="size-3.5" aria-hidden="true" />
            {own ? `Replace ${rule.label.toLowerCase()}` : `Upload ${rule.label.toLowerCase()}`}
          </Button>
          {own && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              loading={remove.isPending}
              disabled={busy}
              aria-label={`Remove ${rule.label.toLowerCase()}`}
              onClick={() =>
                remove.mutate(slot, {
                  onSuccess: () => toast.success(`${rule.label} removed`),
                  onError: (error) => setProblem(brandErrorMessage(error)),
                })
              }
            >
              <Trash2 className="size-3.5" aria-hidden="true" />
            </Button>
          )}
        </div>
      )}
    </li>
  );
}
