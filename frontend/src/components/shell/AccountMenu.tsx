import { useEffect, useRef, useState } from "react";
import { LogIn, LogOut, ShieldCheck, User } from "lucide-react";
import { useMsal } from "@azure/msal-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ENTRA_ENABLED, signIn, signOut } from "@/auth/msal";

/**
 * Top-right account control. Three states:
 * - Entra disabled (mock mode): show a "demo" pill.
 * - Entra enabled, no account: show "Sign in" button.
 * - Entra enabled, signed in: show name + sign-out.
 */
export function AccountMenu() {
  if (!ENTRA_ENABLED) {
    return (
      <Badge
        variant="info"
        title="Mock authentication (development)"
        className="size-8 justify-center p-0 sm:h-auto sm:w-auto sm:gap-1.5 sm:px-2.5 sm:py-0.5"
      >
        <ShieldCheck className="size-3" />
        <span className="hidden sm:inline">Mock auth (dev)</span>
      </Badge>
    );
  }
  return <SignedInOrOut />;
}

function initialsOf(name: string): string {
  return name
    .split(/\s+/)
    .map((w) => w[0])
    .filter(Boolean)
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

function SignedInOrOut() {
  const { accounts } = useMsal();
  const account = accounts[0];
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onPointer = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onPointer);
    return () => document.removeEventListener("mousedown", onPointer);
  }, [open]);

  if (!account) {
    return (
      <Button
        size="sm"
        variant="outline"
        aria-label="Sign in"
        onClick={() => void signIn()}
      >
        <LogIn className="size-4" />
        <span className="hidden sm:inline">Sign in</span>
      </Button>
    );
  }

  const display = account.name || account.username;
  const initials = initialsOf(display || "??");

  return (
    <div ref={menuRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen(!open)}
        aria-label={`Account menu for ${display}`}
        aria-expanded={open}
        aria-haspopup="menu"
        className="flex h-8 items-center gap-2 rounded-md border border-border bg-card px-2 text-sm hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
      >
        <div className="flex size-6 items-center justify-center rounded-full bg-primary text-2xs font-semibold text-primary-foreground">
          {initials}
        </div>
        <span className="hidden max-w-[160px] truncate text-foreground sm:inline">
          {display}
        </span>
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-10 z-50 w-64 rounded-md border border-border bg-card shadow-md"
        >
          <div className="border-b border-border px-3 py-2">
            <div className="flex items-center gap-2 text-sm font-medium text-foreground">
              <User className="size-4" />
              {display}
            </div>
            {account.username && account.username !== display && (
              <div className="mt-0.5 text-xs text-muted-foreground">
                {account.username}
              </div>
            )}
          </div>
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              setOpen(false);
              void signOut();
            }}
            className="flex w-full items-center gap-2 px-3 py-2 text-sm text-foreground hover:bg-muted"
          >
            <LogOut className="size-4" />
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
