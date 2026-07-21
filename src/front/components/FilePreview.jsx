import { useEffect, useState } from "react";

// Read shared files inside the platform instead of downloading them.
// PDFs render in an embedded viewer, images/video/audio play inline, text and
// CSV are fetched and shown; anything else falls back to "open/download".
// Downloading stays available as an explicit choice, never the only option.
const TEXTUAL = /(^text\/)|json|csv|xml|yaml|markdown/i;

export const FilePreview = ({ url, mediaType, name, onClose }) => {
  const [text, setText] = useState(null);
  const [error, setError] = useState(null);
  const isPdf = /pdf/i.test(mediaType || "") || /\.pdf($|\?)/i.test(url || "");
  const isImage = (mediaType || "").startsWith("image/");
  const isVideo = (mediaType || "").startsWith("video/");
  const isAudio = (mediaType || "").startsWith("audio/");
  const isText = TEXTUAL.test(mediaType || "");

  useEffect(() => {
    if (!isText) return;
    fetch(url)
      .then((r) => (r.ok ? r.text() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((t) => setText(t.slice(0, 200000)))
      .catch((e) => setError(e.message));
  }, [url, isText]);

  return (
    <div className="cd-backdrop" onClick={onClose}>
      <div className="co-card fp-modal" onClick={(e) => e.stopPropagation()}>
        <div className="fp-head">
          <b>{name || "Attachment"}</b>
          <span className="muted" style={{ fontSize: ".74rem" }}>{mediaType}</span>
          <span style={{ flex: 1 }} />
          <a className="btn btn-sm btn-outline-secondary" href={url}
            download={name || true} target="_blank" rel="noreferrer">
            <i className="fa-solid fa-download" /> Download
          </a>
          <button className="btn btn-sm btn-outline-secondary" onClick={onClose}>
            <i className="fa-solid fa-xmark" />
          </button>
        </div>

        <div className="fp-body">
          {isPdf && <iframe title="preview" src={url} className="fp-frame" />}
          {isImage && <img src={url} alt={name} className="fp-img" />}
          {isVideo && <video src={url} controls className="fp-media" />}
          {isAudio && <audio src={url} controls className="fp-audio" />}
          {isText && (
            error ? <div className="alert alert-danger py-2">{error}</div>
              : text === null ? <div className="empty">Loading…</div>
                : <pre className="fp-text">{text}</pre>
          )}
          {!isPdf && !isImage && !isVideo && !isAudio && !isText && (
            <div className="empty">
              No in-platform preview for this file type.<br />
              <a href={url} target="_blank" rel="noreferrer">Open in a new tab</a>
              {" or use Download."}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
