# Android App Development Instructions

This document provides instructions for an AI agent to build a basic Android application that connects to Supabase.

## Objective
Build an Android application (using Kotlin and Jetpack Compose) that allows a user to log in and view their data stored in a Supabase Storage bucket named `user_data`. The app must ensure that users can only see and access their own files.

## Requirements
1. **Authentication:**
   - Implement Supabase Authentication (Email/Password or any suitable provider).
   - The user must be logged in to view their data.

2. **Storage Access:**
   - Connect to the Supabase Storage bucket named `user_data`.
   - The data for each user is stored under a folder named with their Supabase User ID (`{uid}/...`).
   - For example, if a user's ID is `123`, they should only be able to see files in the `user_data` bucket under the path `123/`.
   - Fetch and display a list of files from this path.

3. **Android App Structure:**
   - Provide a standard, basic Android project structure.
   - Use Kotlin.
   - Use Jetpack Compose for the UI.
   - Include `build.gradle.kts` dependencies for Supabase Android SDK (e.g., `io.github.jan-tennert.supabase:gotrue-kt`, `io.github.jan-tennert.supabase:storage-kt`).
   - Create standard packages/files like `MainActivity.kt`, a view model for handling Supabase calls, and UI components for Login and File Listing.

## Environment Variables & Keys
Use the following Supabase credentials to configure the Supabase client in the app:

- **Supabase URL:** `https://mywyztpikujpsvcvbirm.supabase.co`
- **Supabase Anon Key:** `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im15d3l6dHBpa3VqcHN2Y3ZiaXJtIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODQzNTA2MzcsImV4cCI6MjA5OTkyNjYzN30.aEwpaKgXu5xGoVXouNnyUTbjh_-M62moCCl0sxtc2Do`

*(Note: Do not use the Service Role key in the client application for security reasons. Only use the Anon Key and rely on Row Level Security / Storage Policies in Supabase to restrict access.)*

## Key Implementation Details for the Agent
1. **Initialize Supabase Client:**
   Initialize the Supabase client in an Application class or Dependency Injection module using the provided URL and Anon Key.
2. **Login Screen:**
   A simple screen with Email/Password fields and a Login button.
3. **Data Screen (File List):**
   Once authenticated, retrieve the current user's UID via `supabase.auth.currentUserOrNull()?.id`.
   List files using `supabase.storage.from("user_data").list(path = "$uid/")`.
   Display the results in a Compose `LazyColumn`.

Please generate the complete codebase based on these instructions.
