import { useState, useEffect } from "react";
import {
  listFiles, deleteFile,
  listCollections, createCollection, deleteCollection,
  addFileToCollection, removeFileFromCollection, getCollectionsForFile,
} from "./library";
import "./Library.css";

export default function Library({ session, onClose, onSelectScope }) {
  const [files, setFiles] = useState([]);
  const [collections, setCollections] = useState([]);
  const [fileCollectionMap, setFileCollectionMap] = useState({}); // fileId -> [collectionIds]
  const [newCollectionName, setNewCollectionName] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    refresh();
  }, []);

  async function refresh() {
    setLoading(true);
    const [f, c] = await Promise.all([
      listFiles(session.user.id),
      listCollections(session.user.id),
    ]);
    setFiles(f);
    setCollections(c);

    // Build a map of fileId -> which collections it's in
    const map = {};
    await Promise.all(
      f.map(async (file) => {
        map[file.id] = await getCollectionsForFile(file.id);
      })
    );
    setFileCollectionMap(map);
    setLoading(false);
  }

  async function handleCreateCollection(e) {
    e.preventDefault();
    if (!newCollectionName.trim()) return;
    await createCollection(session.user.id, newCollectionName.trim());
    setNewCollectionName("");
    refresh();
  }

  async function handleDeleteCollection(id) {
    if (!confirm("Remove this collection? Files inside it are not deleted.")) return;
    await deleteCollection(id);
    refresh();
  }

  async function handleDeleteFile(id) {
    if (!confirm("Delete this file from your library?")) return;
    await deleteFile(id);
    refresh();
  }

  async function toggleFileInCollection(fileId, collectionId) {
    const inIt = fileCollectionMap[fileId]?.includes(collectionId);
    if (inIt) await removeFileFromCollection(fileId, collectionId);
    else await addFileToCollection(fileId, collectionId);
    refresh();
  }

  const unsortedFiles = files.filter((f) => !fileCollectionMap[f.id]?.length);

  return (
    <div className="library-page">
      <div className="library-header">
        <h1 className="library-title">Your Library</h1>
        <button className="library-close" onClick={onClose}>← Back to chat</button>
      </div>

      {loading ? (
        <p>Loading your library...</p>
      ) : (
        <>
          {/* Ask everything */}
          <button className="scope-all-btn" onClick={() => onSelectScope({ type: "all" })}>
            Ask across all my files →
          </button>

          {/* Collections */}
          <section className="lib-section">
            <h2>Collections</h2>
            <form className="new-collection-form" onSubmit={handleCreateCollection}>
              <input
                type="text"
                placeholder="New collection name..."
                value={newCollectionName}
                onChange={(e) => setNewCollectionName(e.target.value)}
              />
              <button type="submit">Create</button>
            </form>

            {collections.length === 0 && <p className="lib-empty">No collections yet.</p>}

            {collections.map((col) => {
              const filesHere = files.filter((f) => fileCollectionMap[f.id]?.includes(col.id));
              return (
                <div className="collection-card" key={col.id}>
                  <div className="collection-head">
                    <span className="collection-name">{col.name}</span>
                    <div>
                      <button
                        className="scope-btn"
                        onClick={() => onSelectScope({ type: "collection", id: col.id, name: col.name })}
                      >
                        Ask this collection
                      </button>
                      <button className="delete-btn" onClick={() => handleDeleteCollection(col.id)}>✕</button>
                    </div>
                  </div>
                  {filesHere.length === 0 ? (
                    <p className="lib-empty">No files in this collection yet.</p>
                  ) : (
                    filesHere.map((f) => (
                      <div className="file-row" key={f.id}>
                        <span>{f.filename}</span>
                        <button
                          className="unlink-btn"
                          onClick={() => toggleFileInCollection(f.id, col.id)}
                        >
                          Remove
                        </button>
                      </div>
                    ))
                  )}
                </div>
              );
            })}
          </section>

          {/* All files, with collection assignment */}
          <section className="lib-section">
            <h2>All files</h2>
            {files.length === 0 && <p className="lib-empty">No files uploaded yet.</p>}
            {files.map((f) => (
              <div className="file-card" key={f.id}>
                <div className="file-card-head">
                  <span className="file-name">{f.filename}</span>
                  <div>
                    <button
                      className="scope-btn"
                      onClick={() => onSelectScope({ type: "file", id: f.id, name: f.filename })}
                    >
                      Ask this file
                    </button>
                    <button className="delete-btn" onClick={() => handleDeleteFile(f.id)}>✕</button>
                  </div>
                </div>
                <p className="file-meta">{f.chunk_count} chunks · {f.status}</p>
                {collections.length > 0 && (
                  <div className="collection-tags">
                    {collections.map((col) => {
                      const active = fileCollectionMap[f.id]?.includes(col.id);
                      return (
                        <button
                          key={col.id}
                          className={`tag-toggle ${active ? "active" : ""}`}
                          onClick={() => toggleFileInCollection(f.id, col.id)}
                        >
                          {active ? "✓ " : "+ "}{col.name}
                        </button>
                      );
                    })}
                  </div>
                )}
              </div>
            ))}
          </section>

          {/* Unsorted bucket */}
          {unsortedFiles.length > 0 && (
            <section className="lib-section">
              <h2>Unsorted</h2>
              <p className="lib-empty">{unsortedFiles.length} file(s) not yet in a collection.</p>
            </section>
          )}
        </>
      )}
    </div>
  );
}
