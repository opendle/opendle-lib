# Share development cache control

Status: Accepted by the user request on 2026-09-07.

The user requested cache busting in OpenDLE Lib for Router and Ontology,
including development. Both applications serve JavaScript through Vite, so a
Python response helper cannot control these responses.

Add a separate dependency-free Node package in this repository. Consumers use
its direct Git `main` dependency and record the resolved commit in their npm
lock. This follows the shared library update policy and is exempt from the
external release age rule. Do not publish it to npm. The Python package stays
unchanged. UI components and design behavior remain in OpenDLE UI.

The helper disables response caching in development and preview, changes the
dependency optimizer hash for each development server, and watches configured
shared build directories. Hosts own their file delivery and security headers.
Production builds retain Vite content-hashed asset names.
