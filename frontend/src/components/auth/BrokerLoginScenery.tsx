import { useEffect, useRef, useState } from "react";
import { Pause, Play } from "lucide-react";

type Connection = EventTarget & { saveData?: boolean };

/** A still is always present; motion is an optional enhancement to sign-in. */
export function BrokerLoginScenery() {
  const [allowed, setAllowed] = useState(false);
  const [visible, setVisible] = useState(true);
  const [paused, setPaused] = useState(false);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);

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
    if (!visible || paused) {
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
  }, [allowed, visible, paused, failed]);

  return (
    <div className="broker-login__scenery" data-video-ready={ready && !failed}>
      <div className="broker-login__landscape" aria-hidden="true">
        <img
          className="broker-login__poster"
          src="/broker/login/marina-bay-v1.webp"
          width="1080"
          height="1080"
          alt=""
          fetchPriority="high"
        />
        {allowed && !failed && (
          <video
            ref={videoRef}
            className="broker-login__video"
            src="/broker/login/marina-bay-v1.mp4"
            poster="/broker/login/marina-bay-v1.webp"
            muted
            loop
            playsInline
            preload="auto"
            disablePictureInPicture
            tabIndex={-1}
            onPlaying={() => setReady(true)}
            onError={() => { setFailed(true); setReady(false); }}
          />
        )}
      </div>
      {allowed && ready && !failed && (
        <button
          type="button"
          className="broker-login__motion"
          aria-label={paused ? "Play background animation" : "Pause background animation"}
          title={paused ? "Play animation" : "Pause animation"}
          onClick={() => setPaused((value) => !value)}
        >
          {paused ? <Play size={17} aria-hidden="true" /> : <Pause size={17} aria-hidden="true" />}
        </button>
      )}
    </div>
  );
}
