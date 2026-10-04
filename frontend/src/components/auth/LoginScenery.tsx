import { useEffect, useRef, useState } from "react";

type Connection = EventTarget & { saveData?: boolean };

export function LoginScenery() {
  const [moving, setMoving] = useState(false);
  const [mediaAllowed, setMediaAllowed] = useState(false);
  const [videoFailed, setVideoFailed] = useState(false);
  const [videoReady, setVideoReady] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const preference = window.matchMedia("(prefers-reduced-motion: reduce)");
    const connection = (navigator as Navigator & { connection?: Connection }).connection;
    const update = () => {
      const allowed = !preference.matches && !connection?.saveData;
      setMediaAllowed(allowed);
      setMoving(allowed && !document.hidden);
      if (!allowed) setVideoReady(false);
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
    if (!moving) {
      video.pause();
      return;
    }
    let cancelled = false;
    void video.play().catch(() => {
      if (!cancelled) {
        setVideoFailed(true);
        setVideoReady(false);
      }
    });
    return () => {
      cancelled = true;
      video.pause();
    };
  }, [moving, mediaAllowed, videoFailed]);

  return (
    <div className="portal-login__scenery" data-protected-media aria-hidden="true" data-video-ready={videoReady}>
      <div className="portal-login__landscape">
        <img className="portal-login__plate" src="/portal/login/scene-seedance-light-poster.webp" alt="" width="1440" height="1440" fetchPriority="high" />
        {mediaAllowed && !videoFailed && (
          <video
            ref={videoRef}
            className="portal-login__video"
            src="/portal/login/scene-seedance-light.mp4"
            poster="/portal/login/scene-seedance-light-poster.webp"
            muted loop playsInline preload="auto" disablePictureInPicture disableRemotePlayback
            controls={false} controlsList="nodownload nofullscreen noremoteplayback" draggable={false} tabIndex={-1}
            onPlaying={() => setVideoReady(true)}
            onError={() => { setVideoFailed(true); setVideoReady(false); }}
          />
        )}
      </div>
    </div>
  );
}
