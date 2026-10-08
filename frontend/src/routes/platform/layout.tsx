import { Outlet } from "@tanstack/react-router";
import { Loader2, ShieldAlert } from "lucide-react";
import { useMe } from "@/api/hooks";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";

/** Gate for every platform console page. `/me.platform_console` is true only
 *  for a system_admin on a platform host; everyone else is told so and no
 *  platform data is requested (the API refuses it regardless). */
export function PlatformLayout() {
  const { data: me, isPending, isError, refetch } = useMe();

  if (isPending) {
    return (
      <p role="status" className="flex items-center gap-2 p-8 text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" aria-hidden="true" /> Loading…
      </p>
    );
  }

  if (isError || !me) {
    return (
      <Card className="max-w-lg">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm">
            <ShieldAlert className="size-4 text-warn" aria-hidden="true" /> Couldn’t load your account
          </CardTitle>
          <CardDescription>We couldn’t verify your access just now.</CardDescription>
        </CardHeader>
        <CardContent>
          <Button variant="outline" size="sm" onClick={() => void refetch()}>
            Retry
          </Button>
        </CardContent>
      </Card>
    );
  }

  if (me.platform_console !== true) {
    return (
      <Card className="max-w-lg">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-sm">
            <ShieldAlert className="size-4 text-warn" aria-hidden="true" /> Access restricted
          </CardTitle>
          <CardDescription>
            The platform console is available to the platform administrator,
            signed in on the platform&apos;s own web address.
          </CardDescription>
        </CardHeader>
      </Card>
    );
  }

  return (
    <div className="mx-auto max-w-screen-2xl space-y-5">
      <Outlet />
    </div>
  );
}
