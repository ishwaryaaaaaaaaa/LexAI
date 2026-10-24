import { supabase } from "./supabaseClient";
import { apiFetch } from "./api";

// ============================================================
// All the Supabase calls for the Library, in one place.
// Each function does ONE job, so the UI code stays simple.
// ============================================================

// ---- Files ----

export async function recordFile(userId, filename, fileType, chunkCount) {
  const { data, error } = await supabase
    .from("files")
    .insert({
      owner_id: userId,
      filename,
      file_type: fileType,
      chunk_count: chunkCount,
      status: "ready",
    })
    .select()
    .single();
  if (error) throw error;
  return data;
}

export async function listFiles(userId) {
  const { data, error } = await supabase
    .from("files")
    .select("*")
    .eq("owner_id", userId)
    .order("created_at", { ascending: false });
  if (error) throw error;
  return data;
}

// Deletes a file from the backend index first, then from the library.
// `file` is a row from listFiles(); its `filename` is the name the backend
// returned on upload, which is the name its chunks are stored under.
export async function deleteFile(file) {
  try {
    await apiFetch(`/papers/${encodeURIComponent(file.filename)}`, { method: "DELETE" });
  } catch (err) {
    // 404 = the backend has no chunks under that name (already gone): still remove the row.
    // Anything else: keep the row and let the caller show the error.
    if (err.status !== 404) throw err;
  }
  // The backend deletes by filename, so every library row for that filename is now stale.
  const { error } = await supabase
    .from("files")
    .delete()
    .eq("owner_id", file.owner_id)
    .eq("filename", file.filename);
  if (error) throw error;
}

// Finds the user's existing library row for a filename (used for duplicate uploads).
export async function findFileByName(userId, filename) {
  const { data, error } = await supabase
    .from("files")
    .select("*")
    .eq("owner_id", userId)
    .eq("filename", filename)
    .limit(1);
  if (error) throw error;
  return data[0] || null;
}

// ---- Collections ----

export async function createCollection(userId, name, colour = "#1a1a1a") {
  const { data, error } = await supabase
    .from("collections")
    .insert({ owner_id: userId, name, colour })
    .select()
    .single();
  if (error) throw error;
  return data;
}

export async function listCollections(userId) {
  const { data, error } = await supabase
    .from("collections")
    .select("*")
    .eq("owner_id", userId)
    .order("created_at", { ascending: true });
  if (error) throw error;
  return data;
}

export async function deleteCollection(collectionId) {
  const { error } = await supabase.from("collections").delete().eq("id", collectionId);
  if (error) throw error;
}

// ---- File <-> Collection links ----

export async function addFileToCollection(fileId, collectionId) {
  const { error } = await supabase
    .from("file_collections")
    .insert({ file_id: fileId, collection_id: collectionId });
  if (error) throw error;
}

export async function removeFileFromCollection(fileId, collectionId) {
  const { error } = await supabase
    .from("file_collections")
    .delete()
    .eq("file_id", fileId)
    .eq("collection_id", collectionId);
  if (error) throw error;
}

// Get all file_ids belonging to a collection (used for the scope filter)
export async function getFileIdsInCollection(collectionId) {
  const { data, error } = await supabase
    .from("file_collections")
    .select("file_id")
    .eq("collection_id", collectionId);
  if (error) throw error;
  return data.map((row) => row.file_id);
}

// Get all collection_ids a given file belongs to (for showing on a file card)
export async function getCollectionsForFile(fileId) {
  const { data, error } = await supabase
    .from("file_collections")
    .select("collection_id")
    .eq("file_id", fileId);
  if (error) throw error;
  return data.map((row) => row.collection_id);
}
