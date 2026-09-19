"use client";

import { useEffect, useState } from "react";
import { ImageOff } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Skeleton } from "@/components/ui/misc";
import { useT } from "@/lib/i18n";

/**
 * A photo stored by the API. An <img src> cannot send the bearer token, so the
 * bytes are fetched with it and shown from a blob URL — revoked when the image
 * leaves the screen, so a long day on the tablet does not leak memory.
 */
export function AuthedImage({
  url,
  alt,
  className,
  link = false,
}: {
  url: string;
  alt: string;
  className?: string;
  /** Wrap in a link that opens the full photo. */
  link?: boolean;
}) {
  const t = useT();
  const [src, setSrc] = useState<string | null>(null);
  const [failed, setFailed] = useState<string | null>(null);

  useEffect(() => {
    let objectUrl: string | null = null;
    let cancelled = false;
    setSrc(null);
    setFailed(null);
    api
      .attachmentBlob(url)
      .then((blob) => {
        if (cancelled) return;
        objectUrl = URL.createObjectURL(blob);
        setSrc(objectUrl);
      })
      .catch((e: Error) => !cancelled && setFailed(e.message));
    return () => {
      cancelled = true;
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url]);

  if (failed) {
    return (
      <div
        role="img"
        aria-label={t("{0} — could not load", { 0: alt })}
        title={failed}
        className={cn("text-3 surface-2 grid place-items-center rounded-xl border text-xs", className)}
      >
        <span className="flex flex-col items-center gap-1 p-2 text-center">
          <ImageOff className="size-5" />
          {t("Photo did not load")}
        </span>
      </div>
    );
  }
  if (!src) return <Skeleton className={cn("rounded-xl", className)} />;

  // eslint-disable-next-line @next/next/no-img-element -- a blob URL cannot go through next/image
  const image = <img src={src} alt={alt} className={cn("rounded-xl border object-cover", className)} />;
  return link ? (
    <a href={src} target="_blank" rel="noreferrer" className="block" aria-label={t("Open {0}", { 0: alt })}>
      {image}
    </a>
  ) : (
    image
  );
}
