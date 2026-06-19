import { supabase } from "./supabaseClient";

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

export async function deleteFile(fileId) {
  const { error } = await supabase.from("files").delete().eq("id", fileId);
  if (error) throw error;
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
