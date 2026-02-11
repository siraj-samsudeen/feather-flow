Feature: Todo list management

Goal: Allow users to create, organize, and track personal tasks

Scope
In: create task, edit task, delete task, mark complete/incomplete, list tasks
Out: shared lists, recurring tasks, subtasks, due date reminders, tags/labels

Dependencies
- Requires: user authentication
- Triggers: none
- Blocked by: none

Acceptance Criteria

WHEN user submits task with valid title THE SYSTEM SHALL save task as incomplete and display in list
WHEN user marks incomplete task as complete THE SYSTEM SHALL update status and show completion indicator
WHEN user marks complete task as incomplete THE SYSTEM SHALL update status and remove completion indicator
WHEN user edits task title THE SYSTEM SHALL save changes and display updated title
WHEN user deletes task THE SYSTEM SHALL remove task from list permanently
WHEN user opens task list THE SYSTEM SHALL display all tasks belonging to that user

IF user submits empty title THEN THE SYSTEM SHALL display "Title is required"
IF user submits title exceeding 500 characters THEN THE SYSTEM SHALL display "Title too long"

THE SYSTEM SHALL only show tasks belonging to authenticated user (ubiquitous)

Permissions

Standard ownership (user can only access own tasks)

Input Validation

IV1: Title is required
IV2: Title maximum 500 characters

Data Model

T1: tasks
- id (globally unique ID)
- user_id (reference to user)
- title (text)
- completed (yes/no)
- created_at (date/time)
- updated_at (date/time)
