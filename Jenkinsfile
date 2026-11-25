// dSIPRouter release QA: runs the automated part of the QA plan sheet against the QA box
// and writes a "Results <release>" tab back to the sheet.
//
// Jenkins credentials used (create these before the first run):
//   dsip-qa-gui        Username with password  GUI login on the QA box (admin / ...)
//   dsip-qa-ssh        SSH username with private key  root access to the QA box (CLI tests, packet capture)
//   omnibot-google-sa  Secret file             omnibot service-account JSON key (sheet editor)
// Optional, not bound yet: signalwire-sip (ROUTE-002), DSIP_LICENSE_KEY_ALL_MODULES (LIC-001 / API-001, next batch).
//
// The agent needs python3-venv and sipp (Debian: apt install python3-venv sip-tester), and its
// public IP must reach the QA box on UDP 5060 and SSH.

pipeline {
    agent any

    options {
        disableConcurrentBuilds()   // tests share the QA box's routes; never run two at once
        timestamps()
        timeout(time: 60, unit: 'MINUTES')
        buildDiscarder(logRotator(numToKeepStr: '30'))
    }

    parameters {
        string(name: 'RELEASE', defaultValue: '', description: 'Release under test, e.g. v0.792. Names the results tab. Blank = VERSION read from the QA box.')
        string(name: 'DSIP_HOST', defaultValue: 'qa.dsiprouter.net', description: 'QA box (GUI, SIP and SSH)')
        booleanParam(name: 'TLS_INSECURE', defaultValue: false, description: 'Skip GUI certificate checks (needed if DSIP_HOST is an IP)')
        booleanParam(name: 'RUN_PSTN', defaultValue: true, description: 'Include tests that place real, billed PSTN calls (ROUTE-001, ROUTE-002)')
        booleanParam(name: 'RUN_CLI', defaultValue: true, description: 'Include CLI tests (restart dSIPRouter, reset and restore the admin password)')
        booleanParam(name: 'PUBLISH_TO_SHEET', defaultValue: true, description: 'Write results to the QA plan sheet')
    }

    environment {
        QA_OUTDIR = "${WORKSPACE}/results"
        DSIP_HOST = "${params.DSIP_HOST}"
        DSIP_TLS_INSECURE = "${params.TLS_INSECURE ? '1' : '0'}"
    }

    stages {
        stage('Setup') {
            steps {
                sh '''
                    command -v sipp >/dev/null || { echo "sipp is not installed on this agent (apt install sip-tester)"; exit 1; }
                    python3 -m venv .venv
                    .venv/bin/pip install -q -r requirements.txt
                    rm -rf results && mkdir -p results
                '''
            }
        }

        stage('Sheet vs tests') {
            steps {
                withCredentials([file(credentialsId: 'omnibot-google-sa', variable: 'GOOGLE_APPLICATION_CREDENTIALS')]) {
                    script {
                        def rc = sh(returnStatus: true, script: '.venv/bin/python tools/sheet_sync.py check')
                        if (rc == 2) {
                            unstable('The sheet and the tests disagree (see MISSING TEST / UNKNOWN ID above)')
                        } else if (rc != 0) {
                            error('sheet check failed')
                        }
                    }
                }
            }
        }

        stage('Tests') {
            steps {
                withCredentials([
                    usernamePassword(credentialsId: 'dsip-qa-gui', usernameVariable: 'DSIP_GUI_USER', passwordVariable: 'DSIP_QA_PASSWORD'),
                    sshUserPrivateKey(credentialsId: 'dsip-qa-ssh', keyFileVariable: 'DSIP_SSH_KEY', usernameVariable: 'DSIP_SSH_USER'),
                ]) {
                    script {
                        def skip = []
                        if (!params.RUN_PSTN) { skip << 'pstn' }
                        if (!params.RUN_CLI) { skip << 'disruptive' }
                        def marks = skip ? "-m 'not ${skip.join(' and not ')}'" : ''
                        // routing first, CLI last (it restarts services); failures are reported by junit, not by exit code
                        sh """
                            .venv/bin/python -m pytest ${marks} -v -rA \
                                --junitxml=results/junit.xml \
                                tests/test_outbound.py tests/test_failover.py tests/test_inbound.py tests/test_cli.py || true
                        """
                    }
                }
            }
        }
    }

    post {
        always {
            junit allowEmptyResults: true, testResults: 'results/junit.xml'
            archiveArtifacts allowEmptyArchive: true, artifacts: 'results/**'
            script {
                if (params.PUBLISH_TO_SHEET && fileExists('results/results.json')) {
                    // tested.json is written by the test run: {"host", "version", "revision"}
                    def tested = { key -> sh(returnStdout: true, script: """
                        .venv/bin/python -c 'import json,sys; print(json.load(open("results/tested.json")).get(sys.argv[1]) or "")' ${key} 2>/dev/null || true
                    """).trim() }
                    def version = tested('version')
                    def release = params.RELEASE ?: (version ? "v${version}" : "build-${env.BUILD_NUMBER}")
                    def what = "${release} on ${tested('host') ?: params.DSIP_HOST} (${tested('revision') ?: '?'})"
                    withCredentials([file(credentialsId: 'omnibot-google-sa', variable: 'GOOGLE_APPLICATION_CREDENTIALS')]) {
                        sh ".venv/bin/python tools/sheet_sync.py publish --results results/results.json --release '${release}' --build-url '${env.BUILD_URL}' --tested '${what}'"
                    }
                }
            }
        }
    }
}
