"""Off-archive prototypes for future epochs (ROADMAP side tracks).

Nothing in this package touches the verifier-pinned files or the scored
archive. Prototypes import pinned modules read-only, run on the out-of-band
validation problems, and write their artifacts under work_dir()/prototypes/.
They exist so that epoch transitions start from measured code instead of
guesses; when an epoch hands over, the surviving design moves into the main
package behind the re-pinned verifier hash and the prototype stays here as
the historical record.
"""
