import utils.http_session
import utils.transport
import utils.session
import argparse
import json
import logging
import os
import re
import string
import sys
import time
from urllib.parse import urljoin, urlparse, urlunparse

import requests
import selenium.webdriver.support.expected_conditions as EC
import wget
import yt_dlp
from selenium.common import TimeoutException
from selenium.common.exceptions import NoSuchElementException
from selenium.webdriver.remote.webdriver import By
from selenium.webdriver.support.wait import WebDriverWait
from seleniumbase import Driver


def normalize_url(url):
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), parsed.params, parsed.query, ""))


def url_reached(current_url, target_url):
    current = normalize_url(current_url)
    target = normalize_url(target_url)
    return current == target or current.startswith(target + "/") or target in current


def create_folder(course_title):
    root_path = os.path.abspath(os.getcwd())
    course_path = os.path.join(root_path, "courses", course_title)
    os.makedirs(course_path, exist_ok=True)
    return course_path


def clean_string(data):
    logging.debug("Cleaning string: " + data)
    # Remove all non-ASCII characters (including emojis)
    data = data.encode('ascii', 'ignore').decode('ascii')
    # Replace specific characters with "-"
    return data.replace("\n", "-").replace(" ", "-").replace(":", "-") \
        .replace("/", "-").replace("|", "-").replace("*", "").replace("?", "-").replace("<", "-") \
        .replace(">", "-").replace("\"", "-").replace("\\", "-")


def truncate_title_to_fit_file_name(title, max_file_name_length=250):
    # the file name length should not be too long
    # truncate the title to accommodate the max used file extension length and lecture index prefix
    max_title_length = max_file_name_length - len(".mp4.part-Frag0000.part") - 3
    if len(title) > max_title_length:
        turncated_title = title[:max_title_length]
        logging.warning("Truncating title: " + turncated_title)
        return turncated_title
    return title


class TeachableDownloader:
    PROGRESS_FILE = "download-progress.json"

    def __init__(self, verbose_arg=False, complete_lecture_arg=False, user_agent_arg=None, timeout_arg=10,
                 resume_arg=True):
        self.driver = Driver(uc=True, headed=True)
        self.headers = {
            "User-Agent": user_agent_arg,
            "Origin": "https://player.hotmart.com",
            "Referer": "https://player.hotmart.com"
        }
        self.verbose = verbose_arg
        self._complete_lecture = complete_lecture_arg
        self.global_timeout = timeout_arg
        self._resume = resume_arg
        self._progress = self.load_progress()

    def check_elem_exists(self, by, selector, timeout):
        try:
            WebDriverWait(self.driver, timeout=self.global_timeout).until(
                EC.presence_of_element_located((by, selector))
            )
        except NoSuchElementException:
            return False
        except TimeoutException:
            return False
        except Exception:
            return False
        else:
            return True

    def bypass_cloudflare(self):
        if self.driver.capabilities["browserVersion"].split(".")[0] < "115":
            return
        logging.info("Bypassing cloudflare")
        time.sleep(1)
        if self.check_elem_exists(By.ID, "challenge-stage", timeout=self.global_timeout):
            try:
                self.driver.find_element(
                    By.ID, "challenge-stage"
                ).click()  # make sure the challenge is focused
                self.driver.execute_script(
                    '''window.open("''' + self.driver.current_url + """","_blank");"""
                )  # open page in new tab
                input(
                    "\033[93mWarning: Bypassing Cloudflare\nplease click on the captcha checkbox if not done already "
                    "and press enter to continue (do not close any of the tabs)\033[0m"
                )
                self.driver.switch_to.window(
                    window_name=self.driver.window_handles[0]
                )  # switch to first tab
                self.driver.close()  # close first tab
                self.driver.switch_to.window(
                    window_name=self.driver.window_handles[0]
                )  # switch back to new tab
            except Exception as e:
                logging.error("Could not bypass cloudflare: " + str(e))
                return
        else:
            logging.info("No need to bypass cloudflare")
            return

    def run(self, course_url, email, password, login_url, man_login_url):
        logging.info("Starting login")

        if man_login_url is None:
            # Check if login_url is not set
            if login_url is None:
                try:
                    self.find_login(course_url)
                except Exception as e:
                    logging.error("Could not find login: " + str(e), exc_info=self.verbose)
            else:
                self.driver.get(login_url)

            try:
                self.login(email, password)
            except Exception as e:
                logging.error("Could not login: " + str(e), exc_info=self.verbose)
                return
        else:
            start_url = login_url if login_url else course_url
            logging.info("Opening login/start page: " + start_url)
            self.driver.get(start_url)
            logging.info(
                "Log in manually (OTP if prompted), then open your course. "
                "Waiting for URL to match: " + man_login_url
            )
            while not url_reached(self.driver.current_url, man_login_url):
                time.sleep(3)
                logging.info("Waiting for url match: " + man_login_url)
                logging.info("Current url: " + self.driver.current_url)

        logging.info("Login detected. Starting download of course: " + course_url)
        try:
            self.pick_course_downloader(course_url)
        except Exception as e:
            logging.error("Could not download course: " + course_url + " cause: " + str(e))

    def run_batch(self, url_array, email, password, login_url, man_login_url):
        """
        This method handles batch downloading of courses. It navigates to the given URLs, logs in if necessary,
        and initiates the download process for each course.

        :param url_array: List[str]
            An array of URLs pointing to the courses that need to be downloaded.
        :param email: str
            The email address used to log in to the platform.
        :param password: str
            The password associated with the provided email address.
        :param login_url: str
            The URL of the login page. If not provided, manual login is assumed.
        :param man_login_url: str
            The URL of the page to navigate to after manual login. This parameter is optional.
            If provided, the script will wait until the user has manually navigated to this URL
            before starting the download process.
        :return: None
        """
        logging.info("Starting login")

        if man_login_url is None:
            # Check if login_url is not set
            if login_url is not None:
                self.driver.get(login_url)
            else:
                logging.error("Login url is not set")
                return

            try:
                self.login(email, password)
            except Exception as e:
                logging.error("Could not login: " + str(e), exc_info=self.verbose)
                return
        else:
            start_url = login_url if login_url else url_array[0]
            logging.info("Opening login/start page: " + start_url)
            self.driver.get(start_url)
            logging.info(
                "Log in manually (OTP if prompted), then open your course. "
                "Waiting for URL to match: " + man_login_url
            )
            while not url_reached(self.driver.current_url, man_login_url):
                time.sleep(3)
                logging.info("Waiting for url match: " + man_login_url)
                logging.info("Current url: " + self.driver.current_url)

        logging.info("Running batch download of courses ")
        for url in url_array:
            try:
                self.pick_course_downloader(url)
            except Exception as e:
                logging.error("Could not download course: " + url + " cause: " + str(e))

    def construct_sign_in_url(self, course_url):
        parsed_url = urlparse(course_url)
        # Replace the path with '/sign_in'
        sign_in_path = '/sign_in'
        fallback_url = urlunparse((parsed_url.scheme, parsed_url.netloc, sign_in_path, '', '', ''))
        return fallback_url

    def find_login(self, course_url):
        logging.info("Trying to find login")

        self.driver.implicitly_wait(self.global_timeout)
        self.driver.get(course_url)

        try:
            login_element = WebDriverWait(self.driver, self.global_timeout).until(
                EC.presence_of_element_located((By.LINK_TEXT, "Login"))
            )
        except TimeoutException:
            logging.warning("Login button not found, navigating to fallback URL")
            fallback_url = self.construct_sign_in_url(course_url)
            self.driver.get(fallback_url)
        else:
            login_element.click()

    def login(self, email, password):
        logging.info("Logging in")

        if self.check_elem_exists(By.ID, "challenge-stage", timeout=self.global_timeout):
            self.bypass_cloudflare()

        WebDriverWait(self.driver, timeout=15).until(
            EC.presence_of_element_located((By.TAG_NAME, 'body')))

        email_element = WebDriverWait(self.driver, self.global_timeout).until(
            EC.presence_of_element_located((By.ID, "email")))
        password_element = WebDriverWait(self.driver, self.global_timeout).until(
            EC.presence_of_element_located((By.ID, "password")))
        commit_element = WebDriverWait(self.driver, self.global_timeout).until(
            EC.presence_of_element_located((By.NAME, "commit")))

        logging.debug("Filling in login form")
        email_element.click()
        email_element.clear()
        self.driver.execute_script("document.getElementById('email').value='" + email + "'")

        password_element.click()
        password_element.clear()
        self.driver.execute_script("document.getElementById('password').value='" + password + "'")

        commit_element.click()

        # Check for login error due to incorrect credentials
        logging.debug("Checking for login error")
        try:
            error_elements = WebDriverWait(self.driver, self.global_timeout).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.toast, span.text-with-icon"))
            )
            for element in error_elements:
                if "Your email or password is incorrect" in element.text:
                    logging.error("Login failed: Incorrect email or password.")
                    return False
        except TimeoutException:
            # No error message found, continue
            pass

        # Check for new device challenge
        # input with name otp_code
        if self.check_elem_exists(By.NAME, "otp_code", timeout=self.global_timeout):
            # wait for user to enter code
            input(
                "\033[93mWarning: New device challenge\nplease enter the code sent to your email and press enter to "
                "continue\033[0m"
            )
        logging.info("Logged in, switching to course page")
        time.sleep(3)

    def pick_course_downloader(self, course_url):
        # Check if we are already on the course page
        if not url_reached(self.driver.current_url, course_url):
            logging.info("Switching to course page")
            self.driver.get(course_url)
            if self.check_elem_exists(By.ID, "challenge-stage", timeout=self.global_timeout):
                self.bypass_cloudflare()

        WebDriverWait(self.driver, timeout=self.global_timeout).until(
            EC.presence_of_element_located((By.TAG_NAME, 'body')))

        # https://support.teachable.com/hc/en-us/articles/360058715732-Course-Design-Templates
        logging.info("Picking course downloader")
        if self.driver.find_elements(By.ID, "__next"):
            logging.info('Choosing __next format')
            self.download_course_simple(course_url)
        elif self.driver.find_elements(By.CLASS_NAME, "course-mainbar"):
            logging.info('Choosing course-mainbar format')
            self.download_course_classic(course_url)
        elif self.driver.find_elements(By.CSS_SELECTOR, ".block__curriculum"):
            logging.info('Choosing .block__curriculum format')
            self.download_course_colossal(course_url)
        else:
            logging.error("Downloader does not support this course template. Please open an issue on github.")

    def download_course_colossal(self, course_url):
        logging.info("Detected block course format")
        try:
            logging.info("Getting course title")
            course_title = WebDriverWait(self.driver, self.global_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, ".course__title"))
            ).text
        except Exception as e:
            logging.warning("Could not get course title, using tab title instead")
            course_title = self.driver.title

        course_title = clean_string(course_title)
        course_path = create_folder(course_title)

        logging.info("Saving course html")
        try:
            output_file = os.path.join(course_path, "course.html")
            with open(output_file, 'w+') as f:
                f.write(self.driver.page_source)
        except Exception as e:
            logging.error("Could not save course html: " + str(e), exc_info=self.verbose)

        # Unhide all elements
        logging.info("Unhiding all elements")
        self.driver.execute_script('[...document.querySelectorAll(".hidden")].map(e=>e.classList.remove("hidden"))')

        chapter_idx = 1
        video_list = []
        sections = WebDriverWait(self.driver, self.global_timeout).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".block__curriculum__section"))
        )

        for section in sections:
            chapter_title = section.find_element(By.CSS_SELECTOR, ".block__curriculum__section__title").text
            chapter_title = clean_string(chapter_title)
            chapter_title = "{:02d}-{}".format(chapter_idx, chapter_title)
            logging.info("Found chapter: " + chapter_title)

            download_path = os.path.join(course_path, chapter_title)
            os.makedirs(download_path, exist_ok=True)

            chapter_idx += 1
            idx = 1

            section_items = section.find_elements(By.CSS_SELECTOR, ".block__curriculum__section__list__item__link")
            for section_item in section_items:
                lecture_link = section_item.get_attribute("href")

                lecture_title = section_item.find_element(By.CSS_SELECTOR,
                                                          ".block__curriculum__section__list__item__lecture-name").text
                lecture_title = clean_string(lecture_title)
                lecture_title = ''.join(char for char in lecture_title if char in string.printable)
                logging.info("Found lecture: " + lecture_title)

                truncated_lecture_title = truncate_title_to_fit_file_name(lecture_title)

                video_entity = {"link": lecture_link, "title": truncated_lecture_title, "idx": idx,
                                "download_path": download_path}
                video_list.append(video_entity)
                idx += 1

        self.download_videos_from_links(video_list)

    def download_course_classic(self, course_url):
        # self.driver.find_elements(By.CLASS_NAME, "course-mainbar")
        logging.info("Detected _mainbar course format")
        try:
            logging.debug("Getting course title")
            course_title = WebDriverWait(self.driver, self.global_timeout).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "body > section > div.course-sidebar > div > h2"))
            ).text
        except Exception as e:
            logging.warning("Could not get course title, using tab title instead")
            course_title = self.driver.title

        logging.debug("Found course title: \"" + course_title + "\" starting cleaning of title string")
        course_title = clean_string(course_title)
        logging.info("Found course title: " + course_title)
        course_path = create_folder(course_title)

        try:
            logging.debug("Saving course html")
            output_file = os.path.join(course_path, "course.html")
            with open(output_file, 'w+', encoding="utf-8") as f:
                f.write(self.driver.page_source)
        except Exception as e:
            logging.error("Could not save course html: " + str(e), exc_info=self.verbose)

        # Get course image

        try:
            image_element = self.driver.find_elements(By.CLASS_NAME, "course-image")
            logging.info("Found course image")
            image_link = image_element[0].get_attribute("src")
            image_link_hd = re.sub(r"/resize=.+?/", "/", image_link)
            # try to download the image using the modified link first
            response = requests.get(image_link_hd)
            if response.ok:
                # save the image to disk
                image_path = os.path.join(course_path, "course-image.jpg")
                with open(image_path, "wb") as f:
                    f.write(response.content)
                logging.info("Image downloaded successfully.")
            else:
                # try to download the image using the original link
                response = requests.get(image_link)
                if response.ok:
                    # save the image to disk
                    image_path = os.path.join(course_path, "course-image.jpg")
                    with open(image_path, "wb") as f:
                        f.write(response.content)
                    logging.info("Image downloaded successfully.")
                else:
                    # print a message indicating that the image download failed
                    logging.warning("Failed to download image.")
        except Exception as e:
            logging.warning("Could not find course image: " + str(e))
            pass

        chapter_idx = 1
        video_list = []
        sections = WebDriverWait(self.driver, 10).until(
            EC.presence_of_all_elements_located((By.CSS_SELECTOR, ".course-section"))
        )
        for section in sections:
            chapter_title = section.find_element(By.CSS_SELECTOR, ".section-title").text
            chapter_title = clean_string(chapter_title)
            chapter_title = chapter_title = "{:02d}-{}".format(chapter_idx, chapter_title)
            logging.info("Found chapter: " + chapter_title)

            download_path = os.path.join(course_path, chapter_title)
            os.makedirs(download_path, exist_ok=True)

            chapter_idx += 1
            idx = 1

            section_items = section.find_elements(By.CSS_SELECTOR, ".section-item")
            for section_item in section_items:
                lecture_link = section_item.find_element(By.CLASS_NAME, "item").get_attribute("href")

                lecture_title = section_item.find_element(By.CLASS_NAME, "lecture-name").text
                lecture_title = clean_string(lecture_title)
                logging.info("Found lecture: " + lecture_title)

                truncated_lecture_title = truncate_title_to_fit_file_name(lecture_title)

                video_entity = {"link": lecture_link, "title": truncated_lecture_title, "idx": idx,
                                "download_path": download_path}
                video_list.append(video_entity)
                idx += 1

        self.download_videos_from_links(video_list)

    def get_course_title_next(self, course_url):
        if self.driver.current_url != course_url:
            self.driver.get(course_url)

        wrap = WebDriverWait(self.driver, self.global_timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".wrap")))
        heading = WebDriverWait(self.driver, self.global_timeout).until(
            EC.presence_of_element_located((By.CSS_SELECTOR, ".heading")))
        course_title = heading.text

        course_title = clean_string(course_title)
        return course_title

    def download_course_simple(self, course_url):
        self.driver.implicitly_wait(2)
        logging.info("Detected next course format")
        course_title = self.get_course_title_next(course_url)
        logging.info("Found course title: " + course_title)
        course_path = create_folder(course_title)

        output_file = os.path.join(course_path, "course.html")
        try:
            with open(output_file, 'w+', encoding='utf-8') as f:
                f.write(self.driver.page_source)
        except Exception as e:
            logging.error("Could not save course html: " + str(e), exc_info=self.verbose)

        # Download course image
        try:
            logging.info("Downloading course image")
            image_element = self.driver.find_element(By.XPATH, "//*[@id=\"__next\"]/div/div/div[2]/div/div[1]/img")
            logging.info("Found course image")
            image_link = image_element.get_attribute("src")
            # Save image
            image_path = os.path.join(course_path, "course-image.jpg")
            # send a GET request to the image link
            try:
                response = requests.get(image_link)
                # write the image data to a file
                with open(image_path, "wb") as f:
                    f.write(response.content)
                # print a message indicating that the image was downloaded
                logging.info("Image downloaded successfully.")
            except Exception as e:
                # print a message indicating that the image download failed
                logging.warning("Failed to download image:" + str(e))
        except Exception as e:
            logging.warning("Could not find course image: " + str(e))
            pass

        chapter_idx = 0
        video_list = []
        slim_sections = self.driver.find_elements(By.CSS_SELECTOR, ".slim-section")
        for slim_section in slim_sections:
            chapter_idx += 1
            bars = slim_section.find_elements(By.CSS_SELECTOR, ".bar")
            chapter_title = slim_section.find_element(By.CSS_SELECTOR, ".heading").text
            chapter_title = clean_string(chapter_title)
            chapter_title = "{:02d}-{}".format(chapter_idx, chapter_title)
            logging.info("Found chapter: " + chapter_title)

            try:
                not_available_element = WebDriverWait(slim_section, self.global_timeout).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, ".drip-tag")))
                logging.warning('Chapter "%s" not available, skipping', chapter_title)
                continue
            except TimeoutException:
                logging.info("Chapter is available")
                pass  # Element wasn't found so the chapter is available

            download_path = os.path.join(course_path, chapter_title)
            os.makedirs(download_path, exist_ok=True)

            idx = 1
            for bar in bars:
                video = bar.find_element(By.CSS_SELECTOR, ".text")
                link = video.get_attribute("href")
                # Remove new line characters from the title and replace spaces with -
                title = clean_string(video.text)
                logging.info("Found lecture: " + title)
                truncated_title = truncate_title_to_fit_file_name(title)
                video_entity = {"link": link, "title": truncated_title, "idx": idx, "download_path": download_path}
                video_list.append(video_entity)
                idx += 1

        self.download_videos_from_links(video_list)

    def load_progress(self):
        if os.path.isfile(self.PROGRESS_FILE):
            try:
                with open(self.PROGRESS_FILE, encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError) as e:
                logging.warning("Could not read progress file, starting fresh: " + str(e))
        return {"lectures": {}}

    def save_progress(self):
        try:
            with open(self.PROGRESS_FILE, "w", encoding="utf-8") as f:
                json.dump(self._progress, f, indent=2)
        except OSError as e:
            logging.warning("Could not save progress file: " + str(e))

    def should_skip_lecture(self, video):
        if not self._resume:
            return False

        entry = self._progress.get("lectures", {}).get(video["link"])
        if not entry or not entry.get("completed"):
            return False

        download_path = video["download_path"]
        video_file = entry.get("video_file")
        if video_file:
            file_path = os.path.join(download_path, video_file)
            if not os.path.isfile(file_path) or os.path.getsize(file_path) < 10000:
                logging.info("Resume: re-downloading (video missing): " + video["title"])
                self.clean_partial_downloads(download_path, video["idx"], video["title"])
                return False

        for attachment_file in entry.get("attachment_files", []):
            file_path = os.path.join(download_path, attachment_file)
            if not os.path.isfile(file_path):
                logging.info("Resume: re-downloading (attachment missing): " + video["title"])
                return False

        return True

    def mark_lecture_complete(self, video, video_file=None, attachment_files=None, html_file=None):
        if "lectures" not in self._progress:
            self._progress["lectures"] = {}

        self._progress["lectures"][video["link"]] = {
            "title": video["title"],
            "section": os.path.basename(video["download_path"]),
            "video_file": video_file,
            "attachment_files": attachment_files or [],
            "html_file": html_file,
            "completed": True,
        }
        self.save_progress()

    def clean_partial_downloads(self, output_path, video_index, title):
        if not os.path.isdir(output_path):
            return

        prefix = "{:02d}-{}".format(video_index, title)
        partial_suffixes = (".part", ".ytdl", ".temp", ".frag")
        removed = 0
        for name in os.listdir(output_path):
            if not name.startswith(prefix):
                continue
            if any(name.endswith(suffix) for suffix in partial_suffixes) or ".part-" in name:
                try:
                    os.remove(os.path.join(output_path, name))
                    removed += 1
                except OSError as e:
                    logging.debug("Could not remove partial file " + name + ": " + str(e))
                continue
            if name.endswith(".mp4"):
                file_path = os.path.join(output_path, name)
                if os.path.getsize(file_path) < 10000:
                    try:
                        os.remove(file_path)
                        removed += 1
                    except OSError as e:
                        logging.debug("Could not remove corrupt file " + name + ": " + str(e))

        if removed:
            logging.info("Resume: cleaned %d partial file(s) for: %s", removed, title)

    def download_videos_from_links(self, video_list):
        total = len(video_list)
        skipped = sum(1 for video in video_list if self.should_skip_lecture(video))
        if self._resume:
            logging.info(
                "Auto-resume: %d/%d lectures complete, %d remaining",
                skipped, total, total - skipped
            )

        for video in video_list:
            if self.should_skip_lecture(video):
                logging.info("Resume: skipping complete lecture: " + video["title"])
                continue

            if self.driver.current_url != video["link"]:
                logging.info("Navigating to lecture: " + video["title"])
                self.driver.get(video["link"])
                self.driver.implicitly_wait(self.global_timeout)

            logging.info("Downloading lecture: " + video["title"])
            self.disable_autoplay()

            has_player = False
            try:
                self.wait_for_video_iframe(timeout=45)
                has_player = len(self.driver.find_elements(
                    By.XPATH, "//iframe[starts-with(@data-testid, 'embed-player')]"
                )) > 0
            except TimeoutException:
                logging.warning("No video iframe found for lecture: " + video["title"])

            html_file = "{:02d}-{}.html".format(video["idx"], video["title"])
            html_path = os.path.join(video["download_path"], html_file)
            if self._resume and os.path.isfile(html_path):
                logging.info("Skipping existing html: " + html_file)
            else:
                try:
                    logging.info("Saving html")
                    self.save_webpage_as_html(video["title"], video["idx"], video["download_path"])
                except Exception as e:
                    logging.error(
                        "Could not save html: " + video["title"] + " cause: " + str(e),
                        exc_info=self.verbose
                    )

            attachment_files = []
            try:
                logging.info("Downloading attachments")
                attachment_files = self.download_attachments(
                    video["link"], video["title"], video["idx"], video["download_path"]
                )
            except Exception as e:
                logging.warning("Could not download attachments: " + video["title"] + " cause: " + str(e))

            video_file = None
            video_downloaded = False
            try:
                logging.debug("Trying to download video as an attachment")
                if self.download_video_file(video["title"], video["idx"], video["download_path"]):
                    video_downloaded = True
                    video_file = "{:02d}-{}.mp4".format(video["idx"], video["title"])
            except Exception as e:
                logging.debug("Could not download video as an attachment: " + video["title"] + " cause: " + str(e))

            if not video_downloaded and has_player:
                iframe_count = len(self.driver.find_elements(
                    By.XPATH, "//iframe[starts-with(@data-testid, 'embed-player')]"
                ))
                for i in range(iframe_count):
                    video_title = video["title"] + ("-" + str(i + 1) if iframe_count > 1 else "")
                    expected_file = "{:02d}-{}.mp4".format(video["idx"], video_title)

                    if self.video_already_downloaded(video["download_path"], video["idx"], video_title):
                        logging.info("Skipping existing video: " + video_title)
                        video_downloaded = True
                        video_file = expected_file
                        continue

                    self.clean_partial_downloads(video["download_path"], video["idx"], video_title)

                    link = self.extract_encrypted_video_url(iframe_index=i, timeout=45)
                    if not link:
                        logging.warning("Could not find video URL for: " + video_title)
                        continue

                    if self.download_video(
                        link, video_title, video["idx"], video["download_path"], retries=3
                    ):
                        video_downloaded = True
                        video_file = expected_file
                        try:
                            logging.info("Downloading subtitle")
                            self.download_subtitle(
                                link, video_title, video["idx"], video["download_path"]
                            )
                        except Exception as e:
                            logging.warning(
                                "Could not download subtitle: " + video_title + " cause: " + str(e)
                            )
                    else:
                        logging.warning("Re-extracting URL and retrying download for: " + video_title)
                        link = self.extract_encrypted_video_url(iframe_index=i, timeout=45)
                        if link and self.download_video(
                            link, video_title, video["idx"], video["download_path"], retries=2
                        ):
                            video_downloaded = True
                            video_file = expected_file

            lecture_success = video_downloaded or not has_player
            if lecture_success:
                logging.info("Finished lecture: " + video["title"])
                self.mark_lecture_complete(
                    video,
                    video_file=video_file,
                    attachment_files=attachment_files,
                    html_file=html_file if os.path.isfile(html_path) else None,
                )
            else:
                logging.error("Video was NOT downloaded for lecture: " + video["title"])

            if self._complete_lecture:
                try:
                    logging.info("Completing lecture")
                    self.complete_lecture()
                except Exception as e:
                    logging.warning("Could not complete lecture: " + video["title"] + " cause: " + str(e))

        if self._resume:
            completed = sum(
                1 for video in video_list
                if self._progress.get("lectures", {}).get(video["link"], {}).get("completed")
            )
            logging.info("Auto-resume: %d/%d lectures now complete", completed, total)

        return

    def complete_lecture(self):
        # Complete lecture
        self.driver.switch_to.default_content()
        complete_button = self.driver.find_element(By.ID, "lecture_complete_button")
        if complete_button:
            logging.info("Found complete button")
            complete_button.click()
            logging.info("Completed lecture")
            time.sleep(3)

    def expected_video_path(self, output_path, video_index, title):
        return os.path.join(output_path, "{:02d}-{}.mp4".format(video_index, title))

    def video_already_downloaded(self, output_path, video_index, title, min_bytes=10000):
        file_path = self.expected_video_path(output_path, video_index, title)
        return os.path.isfile(file_path) and os.path.getsize(file_path) >= min_bytes

    def disable_autoplay(self):
        self.driver.switch_to.default_content()
        try:
            self.driver.execute_script(
                'var checkbox = document.getElementById("custom-toggle-autoplay");'
                'if (checkbox && checkbox.checked) { checkbox.click(); }'
            )
            logging.info("Autoplay disabled")
        except Exception as e:
            logging.debug("Could not disable autoplay: " + str(e))

    def pause_embedded_player(self):
        try:
            self.driver.execute_script(
                'document.querySelectorAll("video").forEach(function(video) {'
                '  video.pause();'
                '  video.autoplay = false;'
                '  video.currentTime = 0;'
                '});'
            )
            logging.info("Paused embedded player")
        except Exception as e:
            logging.debug("Could not pause embedded player: " + str(e))

    def wait_for_video_iframe(self, timeout=45):
        self.driver.switch_to.default_content()
        WebDriverWait(self.driver, timeout).until(
            EC.presence_of_element_located((By.XPATH, "//iframe[starts-with(@data-testid, 'embed-player')]"))
        )

    def extract_encrypted_video_url(self, iframe_index=0, timeout=45):
        self.driver.switch_to.default_content()
        iframes = self.driver.find_elements(
            By.XPATH, "//iframe[starts-with(@data-testid, 'embed-player')]"
        )
        if not iframes or iframe_index >= len(iframes):
            return None

        self.driver.switch_to.frame(iframes[iframe_index])
        self.pause_embedded_player()

        def _read_url(driver):
            try:
                script_text = driver.find_element(By.ID, "__NEXT_DATA__")
                json_text = json.loads(script_text.get_attribute("innerHTML"))
                media_assets = json_text["props"]["pageProps"]["applicationData"]["mediaAssets"]
                if media_assets and media_assets[0].get("urlEncrypted"):
                    return media_assets[0]["urlEncrypted"]
            except Exception:
                return None
            return None

        try:
            link = WebDriverWait(self.driver, timeout).until(lambda d: _read_url(d))
            logging.info("Extracted encrypted video URL")
            return link
        except TimeoutException:
            logging.warning("Timed out waiting for encrypted video URL")
            return None
        finally:
            self.driver.switch_to.default_content()

    def download_video(self, link, title, video_index, output_path, retries=3):
        out_path = self.expected_video_path(output_path, video_index, title)
        if self.video_already_downloaded(output_path, video_index, title):
            logging.info("Skipping existing video: " + title)
            return True

        self.clean_partial_downloads(output_path, video_index, title)

        ydl_opts = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "merge_output_format": "mp4",
            "postprocessors": [
                {
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": "mp4",
                },
                {
                    "key": "FFmpegMetadata",
                },
            ],
            "http_headers": self.headers,
            "concurrentfragments": 15,
            "outtmpl": out_path.replace(".mp4", ""),
            "verbose": self.verbose,
            "retries": 10,
            "fragment_retries": 10,
            "socket_timeout": 30,
            "continuedl": True,
        }

        for attempt in range(1, retries + 1):
            try:
                logging.info("Downloading video (attempt %d/%d): %s", attempt, retries, title)
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([link])
                if self.video_already_downloaded(output_path, video_index, title):
                    logging.info("Downloaded video: " + title)
                    return True
                logging.warning("Download finished but file missing or too small: " + title)
            except Exception as e:
                logging.error(
                    "Could not download video (attempt %d/%d): %s cause: %s",
                    attempt, retries, title, str(e)
                )
            time.sleep(2)

        logging.error("Failed to download video after retries: " + title)
        return False

    # This function is needed because yt-dlp subtitle downloader is not working
    def download_subtitle(self, link, title, video_index, output_path):
        ydl_opts = {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "merge_output_format": "mp4",
            "postprocessors": [
                {
                    "key": "FFmpegVideoConvertor",
                    "preferedformat": "mp4",
                },
                {
                    "key": "FFmpegMetadata",
                },
            ],
            "http_headers": self.headers,
            "allsubtitles": True,
            "subtitleslangs": ["all"],
            "concurrentfragments": 10,
            "writesubtitles": True,
            "outtmpl": os.path.join(output_path, title),
            "verbose": self.verbose,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(link, download=False)
                info_json = ydl.sanitize_info(info)
        except Exception as e:
            logging.warning("Could not download subtitle: " + title + " cause: " + str(e))

        subtitle_links = {}
        for lang, sub_info in info_json["requested_subtitles"].items():
            subtitle_links[lang] = {"url": sub_info["url"], "ext": sub_info["ext"]}

        # Print the subtitle links and language names
        req = None
        for lang, sub in subtitle_links.items():
            subtitle_filename = "{:02d}-{}.{}.{}".format(video_index, title, lang, sub["ext"])
            file_path = os.path.join(output_path, subtitle_filename)
            if os.path.isfile(file_path):
                logging.info("Skipping existing subtitle: " + subtitle_filename)
            else:
                base_url = sub["url"]
                try:
                    req = requests.get(sub["url"], headers=self.headers)
                except Exception as e:
                    logging.warning("Could not download subtitle: " + title + " cause: " + str(e))
                relative_path = req.text.split("\n")[5]
                full_url = urljoin(base_url, relative_path)
                try:
                    response = requests.get(full_url, headers=self.headers)
                    with open(file_path, "wb") as f:
                        f.write(response.content)
                except Exception as e:
                    logging.warning("Could not download subtitle: " + title + " cause: " + str(e))
                logging.info("Downloaded subtitle: " + subtitle_filename)
                
    def download_video_file(self, title, video_index, output_path, timeout=-1):
        video_title = "{:02d}-{}".format(video_index, title)

        # Grab the video attachments type video
        video_attachment = self.driver.find_element(By.CLASS_NAME, "lecture-attachment-type-video")

        if not video_attachment:
            logging.debug(f"No video attachment found for lecture: {title}")
            return False

        video_link = video_attachment.find_element(By.TAG_NAME, "a")

        if not video_link:
            logging.debug(f"No video link found for lecture: {title}")
            return False

        # Set the download directory for this file
        self.driver.execute_cdp_cmd("Page.setDownloadBehavior", {
            "behavior": "allow",
            "downloadPath": output_path
        })
        # Get list of files before download
        files_before_download = set(os.listdir(output_path))

        # Click the link to trigger download
        video_link.click()

        # Wait for download to complete
        start_time = time.time()
        while True:
            files_after_download = set(os.listdir(output_path))

            # Find new files
            new_files = files_after_download - files_before_download

            if len(new_files) == 1 and not list(new_files)[0].endswith('.crdownload'):
                break
            
            if timeout > 0 and (time.time() - start_time) > timeout:
                logging.warning(f"Download timeout for lecture: {title}")
                return False
        
            time.sleep(1)

        latest_file = os.path.join(output_path, list(new_files)[0])
                
        # Determine the file extension
        _, extension = os.path.splitext(latest_file)
        
        # Create the new filename
        new_filename = f"{video_title}{extension}"
        new_filepath = os.path.join(output_path, new_filename)
        
        # Rename the file
        os.rename(latest_file, new_filepath)
        logging.info(f"Downloaded video file {new_filename}")
        return True
    
    def download_attachments(self, link, title, video_index, output_path):
        video_title_prefix = "{:02d}-{}".format(video_index, title)
        found_links = {}

        for block in self.driver.find_elements(By.CLASS_NAME, "lecture-attachment-type-file"):
            for anchor in block.find_elements(By.TAG_NAME, "a"):
                href = anchor.get_attribute("href")
                if href:
                    found_links[href] = anchor.text.strip() or None

        for anchor in self.driver.find_elements(
            By.CSS_SELECTOR, "a[href*='uploads.teachablecdn.com/attachments']"
        ):
            href = anchor.get_attribute("href")
            if href and href not in found_links:
                found_links[href] = anchor.text.strip() or None

        saved_files = []
        if not found_links:
            logging.debug("No attachments found for video: " + title)
            return saved_files

        session = requests.Session()
        for cookie in self.driver.get_cookies():
            session.cookies.set(cookie["name"], cookie["value"])

        for attachment_idx, (attachment_url, display_name) in enumerate(found_links.items(), 1):
            parsed = urlparse(attachment_url)
            url_filename = os.path.basename(parsed.path)
            ext = os.path.splitext(url_filename)[1] or ".pdf"

            if display_name:
                safe_name = clean_string(display_name)
                if not safe_name.lower().endswith(ext.lower()):
                    safe_name += ext
            else:
                safe_name = url_filename if url_filename else "attachment" + ext

            if len(found_links) > 1:
                filename = "{}-{}-{}".format(video_title_prefix, attachment_idx, safe_name)
            else:
                filename = "{}-{}".format(video_title_prefix, safe_name)

            file_path = os.path.join(output_path, filename)
            if os.path.isfile(file_path):
                logging.info("Skipping existing attachment: " + filename)
                saved_files.append(filename)
                continue

            logging.info("Downloading attachment: " + filename)
            try:
                response = session.get(attachment_url, headers=self.headers, timeout=120)
                response.raise_for_status()
                with open(file_path, "wb") as f:
                    f.write(response.content)
                logging.info("Downloaded attachment: " + filename)
                saved_files.append(filename)
            except Exception as e:
                logging.warning(
                    "Could not download attachment: " + filename + " cause: " + str(e)
                )

        return saved_files

    def save_webpage_as_html(self, title, video_index, output_path):
        output_file = os.path.join(output_path, "{:02d}-{}.html".format(video_index, title))
        with open(output_file, 'w+', encoding='utf-8') as f:
            f.write(self.driver.page_source)
        logging.info("Saved webpage as html: " + output_file)

    def save_webpage_as_pdf(self, title, video_index, output_path):
        output_file_pdf = os.path.join(output_path, "{:02d}-{}.pdf".format(video_index, title))
        self.driver.save_print_page(output_file_pdf)
        logging.info("Saved webpage as pdf: " + output_file_pdf)

    def clean_up(self):
        logging.info("Cleaning up")
        self.driver.quit()
        # Delete cookies.txt
        if os.path.exists("cookies.txt"):
            os.remove("cookies.txt")


def read_urls_from_file(file_path):
    urls = []
    try:
        with open(file_path, 'r') as file:
            urls = file.read().splitlines()
    except FileNotFoundError:
        logging.error(f"File not found: {file_path}")
    except IOError as e:
        logging.error(f"IOError reading file: {file_path}. Error: {str(e)}")
    except Exception as e:
        logging.error(f"Unexpected error reading file: {file_path}. Error: {str(e)}")

    if urls:
        logging.info(f"Successfully read {len(urls)} URLs from file: {file_path}")
    else:
        logging.warning(f"No URLs found in file: {file_path}")

    return urls


def check_required_args(args):
    if args.email and args.password:
        return True
    elif args.man_login_url:
        return True
    return False


def setup_logging(log_level, log_file=None):
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog='Teachable-Dl', description='Download courses', )
    parser.add_argument("--url", required=False, help='URL of the course')
    parser.add_argument("-e", "--email", required=False, help='Email of the account')
    parser.add_argument("-p", "--password", required=False, help='Password of the account')
    parser.add_argument('-v', '--verbose', action='count', default=0,
                        help='Increase verbosity level (repeat for more verbosity)')
    parser.add_argument('--complete-lecture', action='store_true', default=False,
                        help='Complete the lecture after downloading')
    parser.add_argument("--login_url", required=False, help='(Optional) URL to teachable SSO login page')
    parser.add_argument("--man_login_url", required=False,
                        help='Login manually and start downloading when this url is reached')
    parser.add_argument("-f", "--file", required=False, help='Path to a text file that contains URLs')
    parser.add_argument("--user-agent", required=False, help='User agent to use when downloading videos',
                        default="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/116.0.0.0 Safari/537.36")
    parser.add_argument("-t", "--timeout", required=False, help='Timeout for selenium driver', default=10)
    parser.add_argument("--log-file", required=False, help='Also write logs to this file')
    parser.add_argument("--no-resume", action="store_true", default=False,
                        help='Disable auto-resume and re-download everything')
    args = parser.parse_args()
    verbose = False
    if args.verbose == 0:
        log_level = logging.WARNING
    elif args.verbose == 1:
        log_level = logging.INFO
    else:
        verbose = True
        log_level = logging.DEBUG

    setup_logging(log_level, args.log_file)

    if not check_required_args(args):
        logging.error("Required arguments are missing. Choose email/password or manual login (man_login_url).")
        exit(1)

    downloader = TeachableDownloader(verbose_arg=verbose, complete_lecture_arg=args.complete_lecture,
                                     user_agent_arg=args.user_agent, timeout_arg=args.timeout,
                                     resume_arg=not args.no_resume)
    if args.file:
        urls = read_urls_from_file(args.file)
        try:
            downloader.run_batch(urls, args.email, args.password, args.login_url, args.man_login_url)
            downloader.clean_up()
            sys.exit(0)
        except KeyboardInterrupt:
            logging.error("Interrupted by user")
            downloader.clean_up()
            sys.exit(1)
        except Exception as e:
            logging.error("Error: " + str(e))
            downloader.clean_up()
            sys.exit(1)
    else:
        # Check if url argument is passed
        if not args.url:
            logging.error("URL is required")
            sys.exit(1)
        try:
            downloader.run(course_url=args.url, email=args.email, password=args.password, login_url=args.login_url,
                           man_login_url=args.man_login_url)
            downloader.clean_up()
            sys.exit(0)
        except KeyboardInterrupt:
            logging.error("Interrupted by user")
            downloader.clean_up()
            sys.exit(1)
        except Exception as e:
            logging.error("Error: " + str(e))
            downloader.clean_up()
            sys.exit(1)
