import cv2


def main():
    camera = cv2.VideoCapture(0)

    try:
        if not camera.isOpened():
            raise RuntimeError('Could not open the webcam.')

        print('Click the preview window and press Q to quit.')

        while True:
            success, frame = camera.read()

            if not success:
                raise RuntimeError('Could not read a camera frame.')

            cv2.imshow('Webcam preview', frame)

            if cv2.waitKey(1) & 0xFF == ord('q'):
                break

    finally:
        camera.release()
        cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
