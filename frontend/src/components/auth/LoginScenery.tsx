import { useEffect, useRef, useState } from "react";
import { brokerLoginMedia, brokerLoginPeriod, type BrokerLoginPeriod } from "@/lib/brokerLoginMedia";

type Connection = EventTarget & { saveData?: boolean };

/** A still is always present; motion is an optional enhancement to sign-in. */
type LoginSceneryProps = { classPrefix?: "broker-login" | "portal-login" };

/** Shared time-based scenery; each login keeps its own scoped styles. */
export function LoginScenery({ classPrefix = "portal-login" }: LoginSceneryProps) {
  const [period, setPeriod] = useState(() => brokerLoginPeriod());

  useEffect(() => {
    const update = () => {
      if (!document.hidden) setPeriod(brokerLoginPeriod());
    };
    const interval = window.setInterval(update, 60_000);
    document.addEventListener("visibilitychange", update);
    return () => {
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", update);
    };
  }, []);

  // A period change disposes of the old player and its readiness/error state.
  // Only the selected clip is ever mounted or downloaded.
  return <LoginMotion key={period} period={period} classPrefix={classPrefix} />;
}

function LoginMotion({ period, classPrefix }: { period: BrokerLoginPeriod; classPrefix: NonNullable<LoginSceneryProps["classPrefix"]> }) {
  const media = brokerLoginMedia(period);
  const [allowed, setAllowed] = useState(false);
  const [visible, setVisible] = useState(true);
  const [inView, setInView] = useState(true);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const sceneryRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const scenery = sceneryRef.current;
    if (!scenery || !window.IntersectionObserver) return;
    const observer = new IntersectionObserver(([entry]) => setInView(entry.isIntersecting));
    observer.observe(scenery);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const connection = (navigator as Navigator & { connection?: Connection }).connection;
    const update = () => {
      const canPlay = !preference.matches && !connection?.saveData;
      setAllowed(canPlay);
      setVisible(!document.hidden);
      if (!canPlay) setReady(false);
    };
    update();
    preference.addEventListener("change", update);
    connection?.addEventListener("change", update);
    document.addEventListener("visibilitychange", update);
    return () => {
      preference.removeEventListener("change", update);
      connection?.removeEventListener("change", update);
      document.removeEventListener("visibilitychange", update);
    };
  }, []);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    if (!allowed || !visible || !inView) {
      video.pause();
      return;
    }
    let cancelled = false;
    void video.play().catch(() => {
      if (!cancelled) {
        setFailed(true);
        setReady(false);
      }
    });
    return () => {
      cancelled = true;
      video.pause();
    };
  }, [allowed, visible, inView, failed]);

  return (
    <div ref={sceneryRef} className={`${classPrefix}__scenery`} data-protected-media aria-hidden="true" data-period={period} data-video-ready={ready && !failed}>
      <div className={`${classPrefix}__landscape`} aria-hidden="true">
        <img
          className={`${classPrefix}__${classPrefix === "portal-login" ? "plate" : "poster"}`}
          src={media.poster}
          width="1080"
          height="1080"
          alt=""
          fetchPriority="high"
          draggable={false}
          onError={event => {
            const image = event.currentTarget;
            if (!image.src.endsWith("/broker/login/marina-bay-v1.webp")) image.src = "/broker/login/marina-bay-v1.webp";
          }}
        />
        {allowed && !failed && (
          <video
            ref={videoRef}
            className={`${classPrefix}__video`}
            src={media.video}
            poster={media.poster}
            muted
            loop
            playsInline
            preload="auto"
            disablePictureInPicture
            disableRemotePlayback
            controls={false}
            controlsList="nodownload nofullscreen noremoteplayback"
            draggable={false}
            tabIndex={-1}
            onPlaying={() => setReady(true)}
            onError={() => { setFailed(true); setReady(false); }}
          />
        )}
      </div>
    </div>
  );
}
