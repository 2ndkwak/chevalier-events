#!/bin/bash
cd /var/www/chevalier
sudo env GIT_SSH_COMMAND="ssh -i /home/trey/.ssh/chevalier_deploy_key -o IdentitiesOnly=yes" git push "$@"
